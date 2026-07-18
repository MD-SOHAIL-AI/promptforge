"""Model routing service used by ForgeX generation."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any

from backend.operations.resilience import BudgetLimits, BudgetTracker, ProviderResilience
from ..services.llm_service import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMService,
)
from .models import ModelRequest, ModelResponse, UsageRecord
from .policy_router import CandidateRejection, DecisionStore, FallbackPolicy, MemoryDecisionStore, ModelCallPolicyRouter, RoutingDecision
from .providers import (
    AnthropicProvider,
    CerebrasProvider,
    BaseModelProvider,
    GeminiProvider,
    GroqProvider,
    LMStudioProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
    NvidiaNimProvider,
)
from .registry import ProviderRegistry
from .usage import UsageTracker


logger = logging.getLogger(__name__)


PROVIDER_ENUMS = {
    # The legacy LLM response contract has no CLI-agent enum. Keep that stable
    # while the model-router response still reports provider_id="codex".
    "openrouter": LLMProvider.OPENROUTER,
    "openai": LLMProvider.OPENAI,
    "groq": LLMProvider.OPENAI,
    "anthropic": LLMProvider.ANTHROPIC,
    "cerebras": LLMProvider.OPENAI,
    "nvidia_nim": LLMProvider.OPENAI,
    "gemini": LLMProvider.GEMINI,
    "ollama": LLMProvider.OLLAMA,
    "lmstudio": LLMProvider.LMSTUDIO,
}

UNAVAILABLE_PROVIDER_HEALTH = {
    "authentication_required",
    "error",
    "not_configured",
    "offline",
    "unavailable",
}


class ModelRouterService(LLMService):
    """Route model requests to configured provider adapters."""

    provider = LLMProvider.OPENROUTER

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        usage: UsageTracker | None = None,
        providers: dict[str, BaseModelProvider] | None = None,
        resilience: ProviderResilience | None = None,
        decision_store: DecisionStore | None = None,
    ) -> None:
        from backend.connection_registry.compatibility import LegacyProviderRegistryFacade
        legacy_registry = registry or ProviderRegistry()
        self.registry = legacy_registry if hasattr(legacy_registry, "connections") else LegacyProviderRegistryFacade(legacy_registry)
        self.usage = usage or UsageTracker()
        initial_route = self.registry.route_for_task("code_generation")
        configured_provider = os.getenv("PROMPTFORGE_LLM_PROVIDER", "").strip().casefold()
        initial_provider = configured_provider if configured_provider in PROVIDER_ENUMS else initial_route.provider_id
        configured_model = os.getenv("PROMPTFORGE_MODEL", "").strip()
        self.provider = PROVIDER_ENUMS.get(initial_provider, LLMProvider.OPENROUTER)
        self.model = configured_model or (
            initial_route.model_id
            if initial_provider == initial_route.provider_id
            else self.registry.default_model(initial_provider)
        )
        self.timeout_s = 180.0
        self.resilience = resilience or ProviderResilience(timeout_seconds=self.timeout_s)
        self.decision_store = decision_store or MemoryDecisionStore()
        self._providers = providers or {
            "openrouter": OpenRouterProvider(self.registry),
            "openai": OpenAIProvider(self.registry),
            "groq": GroqProvider(self.registry),
            "gemini": GeminiProvider(self.registry),
            "anthropic": AnthropicProvider(self.registry),
            "cerebras": CerebrasProvider(self.registry),
            "nvidia_nim": NvidiaNimProvider(self.registry),
            "lmstudio": LMStudioProvider(self.registry),
            "ollama": OllamaProvider(self.registry),
        }

    async def _require_canonical_connection(self, provider_id: str):
        try:
            record = self.registry.connections.refresh_status(f"{provider_id}.default")
        except Exception as exc:
            raise LLMConfigurationError(
                "Provider connection status is unavailable.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="CONFIGURATION_ERROR",
                details={"reason": "canonical_connection_status_unavailable"},
            ) from exc
        if record.credential_status.value == "credential_missing":
            raise LLMConfigurationError(
                f"Model provider {provider_id} is not configured. Add your {self.registry.definition(provider_id).display_name} settings in Models.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="MISSING_CREDENTIALS",
                details={"reason": "credential_missing"},
            )
        if record.auth_state.value == "authentication_failed":
            raise LLMConfigurationError(
                "Provider authentication failed.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="INVALID_CREDENTIALS",
                details={"reason": "authentication_failed"},
            )
        if not record.enabled:
            raise LLMConfigurationError(
                "Model provider is disabled.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="CONFIGURATION_ERROR",
                details={"reason": "connection_disabled"},
            )
        if record.transport_status.value in {"error", "offline"}:
            raise LLMConfigurationError(
                "Provider connection is unavailable.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="CONFIGURATION_ERROR",
                details={"reason": "connection_unhealthy"},
            )
        if record.auth_state.value == "auth_unknown":
            provider = self._providers.get(provider_id)
            if provider is None:
                raise LLMConfigurationError(
                    "Provider authentication could not be verified.",
                    provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                    code="AUTHENTICATION_REQUIRED",
                    details={"reason": "auth_unknown"},
                )
            health = await provider.health()
            record = self.registry.connections.record_transport_result(provider_id, health)
            if record.auth_state.value != "authenticated":
                code = "INVALID_CREDENTIALS" if record.auth_state.value == "authentication_failed" else "AUTHENTICATION_REQUIRED"
                raise LLMConfigurationError(
                    "Provider authentication could not be verified.",
                    provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                    code=code,
                    details={"reason": record.diagnostic_code},
                )
        return record

    async def generate(self, request: LLMRequest | ModelRequest) -> LLMResponse:
        """Generate through the router while preserving the LLMService API."""

        if isinstance(request, ModelRequest):
            model_response = await self.generate_model(request)
            return self._to_llm_response(model_response)
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest or ModelRequest")
        task_type = str(request.metadata.get("task_type", "code_generation"))
        provider_id = request.metadata.get("provider_id")
        model_id = request.metadata.get("model_id")
        allow_fallback = request.metadata.get("allow_fallback")
        local_only = request.metadata.get("local_only")
        model_response = await self.generate_model(
            ModelRequest(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                task_type=task_type,  # type: ignore[arg-type]
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                provider_id=provider_id if isinstance(provider_id, str) else None,
                model_id=model_id if isinstance(model_id, str) else None,
                allow_fallback=allow_fallback if isinstance(allow_fallback, bool) else False,
                local_only=local_only if isinstance(local_only, bool) else False,
                metadata=dict(request.metadata),
            )
        )
        return self._to_llm_response(model_response)

    async def generate_model(self, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        route = self.registry.route_for_task(request.task_type)
        environment_provider = os.getenv("PROMPTFORGE_LLM_PROVIDER", "").strip().casefold()
        environment_route = environment_provider if environment_provider in PROVIDER_ENUMS else None
        preferred_provider = request.provider_id or environment_route or route.provider_id
        environment_model = os.getenv("PROMPTFORGE_MODEL", "").strip() if environment_route else ""
        preferred_model = request.model_id or environment_model or (
            route.model_id if preferred_provider == route.provider_id else self.registry.default_model(preferred_provider)
        )
        preferred_meta = self.registry.provider(preferred_provider)
        await self._require_canonical_connection(preferred_provider)
        fallback_requested = bool(
            request.allow_fallback
            and route.fallback_enabled
            and route.fallback_provider_id
            and route.fallback_provider_id != preferred_provider
        )
        fallback_policy = self._verified_fallback_policy(
            request,
            preferred_provider=preferred_provider,
            fallback_provider=route.fallback_provider_id,
        )
        fallback_authorized = fallback_requested and fallback_policy is not None
        fallback_blocked = fallback_requested and not fallback_authorized
        candidates = self._candidate_provider_ids(
            preferred_provider,
            allow_fallback=fallback_authorized,
            local_only=request.local_only or route.local_only,
            task_type=request.task_type,
            fallback_provider_id=route.fallback_provider_id,
        )
        skip_provider = request.metadata.get("skip_provider_id")
        force_fallback = bool(request.metadata.get("force_fallback", False))
        if (force_fallback or skip_provider == preferred_provider) and not fallback_authorized:
            raise self._fallback_not_authorized(preferred_provider)
        if isinstance(skip_provider, str) and skip_provider.strip():
            if fallback_blocked and skip_provider == preferred_provider:
                raise self._fallback_not_authorized(preferred_provider)
            candidates = tuple(
                provider_id for provider_id in candidates if provider_id != skip_provider
            )
        if not candidates:
            raise LLMConfigurationError(
                "No eligible model route is available.",
                provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
                details={"reason": "no_eligible_model"},
            )
        started = time.monotonic()
        budget = BudgetTracker(BudgetLimits(max_seconds=self.timeout_s, max_steps=max(1, len(candidates)), max_output_tokens=max(1, int(request.max_tokens or 20_000))))
        last_error: Exception | None = None
        for provider_id in candidates:
            provider_meta = self.registry.provider(provider_id)
            model_id = preferred_model if provider_id == preferred_provider else self.registry.default_model(provider_id)
            try:
                await self._require_canonical_connection(provider_id)
            except LLMConfigurationError as exc:
                last_error = exc
                self._record_failure(provider_id, model_id, request.task_type, exc, started)
                break
            try:
                routed_request = ModelRequest(
                    prompt=request.prompt,
                    system_prompt=request.system_prompt,
                    task_type=request.task_type,
                    model_id=model_id,
                    provider_id=provider_id,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    allow_fallback=request.allow_fallback,
                    local_only=request.local_only,
                    metadata=request.metadata,
                )
                decision = self._persist_attempt_decision(request, candidates, provider_id, model_id, fallback_policy)
                response = await self.resilience.execute(provider_id, lambda: self._providers[provider_id].generate(routed_request), timeout_seconds=self.timeout_s, budget=budget)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Model provider failed provider=%s model=%s task=%s",
                    provider_id,
                    model_id,
                    request.task_type,
                )
                self._record_failure(provider_id, model_id, request.task_type, exc, started)
                if not ModelCallPolicyRouter.may_fallback(str(getattr(exc, "code", type(exc).__name__))): break
                if provider_id == preferred_provider and fallback_blocked:
                    raise self._fallback_not_authorized(preferred_provider) from exc
                if provider_id == preferred_provider and not fallback_authorized:
                    break
                continue
            budget.consume(input_tokens=max(0,response.input_tokens or 0), output_tokens=max(0,response.output_tokens or 0), cost_micros=max(0,int((response.estimated_cost or 0)*1_000_000)))
            self.registry.connections.record_transport_success(provider_id)
            self._record_decision_usage(decision, success=True, response=response)
            self._record_usage(
                UsageRecord(
                    provider_id=response.provider_id,
                    model_id=response.model_id,
                    task_type=request.task_type,
                    latency_ms=response.latency_ms,
                    success=True,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    total_tokens=response.total_tokens,
                    estimated_cost=response.estimated_cost,
                )
            )
            self.provider = PROVIDER_ENUMS.get(response.provider_id, LLMProvider.OPENROUTER)
            self.model = response.model_id
            return response
        if last_error is not None:
            if isinstance(last_error, LLMError):
                raise last_error
            raise LLMProviderError(
                "Model provider call failed without an authorized fallback.",
                provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
                retryable=True,
                details={"provider_id": preferred_provider},
            ) from last_error
        raise LLMConfigurationError(
            f"No model route available for {request.task_type}.",
            provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
            details={"task_type": request.task_type},
        )

    @staticmethod
    def _verified_fallback_policy(
        request: ModelRequest,
        *,
        preferred_provider: str,
        fallback_provider: str | None,
    ) -> FallbackPolicy | None:
        if not request.allow_fallback or not fallback_provider:
            return None
        raw_policy = request.metadata.get("fallback_policy")
        try:
            policy = FallbackPolicy(str(raw_policy))
        except (TypeError, ValueError):
            return None
        if fallback_provider == preferred_provider:
            return FallbackPolicy.SAME_PROVIDER if policy is FallbackPolicy.SAME_PROVIDER else None
        recipients = request.metadata.get("approved_recipients")
        approved_recipients = {
            value for value in recipients if isinstance(value, str)
        } if isinstance(recipients, (list, tuple, set, frozenset)) else set()
        if not bool(request.metadata.get("consent_granted", False)):
            return None
        if not {preferred_provider, fallback_provider}.issubset(approved_recipients):
            return None
        if policy is FallbackPolicy.ASK_BEFORE_CROSS_PROVIDER:
            return policy
        if policy is not FallbackPolicy.APPROVED_PROVIDER_GROUP:
            return None
        groups = request.metadata.get("approved_provider_groups")
        approved_groups = {
            value for value in groups if isinstance(value, str) and value
        } if isinstance(groups, (list, tuple, set, frozenset)) else set()
        membership = request.metadata.get("provider_group_membership")
        if not isinstance(membership, Mapping):
            return None
        primary_group = membership.get(preferred_provider)
        fallback_group = membership.get(fallback_provider)
        if not isinstance(primary_group, str) or primary_group != fallback_group:
            return None
        return policy if primary_group in approved_groups else None

    @staticmethod
    def _fallback_not_authorized(preferred_provider: str) -> LLMConfigurationError:
        return LLMConfigurationError(
            "Provider fallback is not authorized for this model call.",
            provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
            code="FALLBACK_NOT_AUTHORIZED",
            details={"reason": "explicit_policy_and_consent_required"},
        )
    def _persist_attempt_decision(self, request: ModelRequest, candidates: tuple[str, ...], provider_id: str, model_id: str, fallback_policy: FallbackPolicy | None) -> RoutingDecision:
        request_id = str(request.metadata.get("request_id") or request.metadata.get("run_id") or f"model-call-{uuid.uuid4().hex}")[:128]
        endpoint_id = f"{provider_id}:{model_id}"
        fallback = tuple(f"{candidate}:{self.registry.default_model(candidate)}" for candidate in candidates if candidate != provider_id)
        decision = RoutingDecision(f"routing.{uuid.uuid4().hex}", request_id, (endpoint_id,), (), endpoint_id, provider_id, model_id, fallback, fallback_policy if fallback and fallback_policy is not None else FallbackPolicy.NONE, bool(request.metadata.get("consent_granted", False)))
        self.decision_store.save(decision)
        return decision

    def _record_decision_usage(self, decision: RoutingDecision, *, success: bool, response: ModelResponse | None = None) -> None:
        usage = {"success": success, "input_units": max(0, response.input_tokens or 0) if response else 0, "output_units": max(0, response.output_tokens or 0) if response else 0, "cost_micros": max(0, int((response.estimated_cost or 0) * 1_000_000)) if response else 0, "selected_provider_id": decision.selected_provider_id, "selected_model_id": decision.selected_model_id}
        try:self.decision_store.record_usage(decision.decision_id, usage)
        except Exception:logger.exception("Routing decision usage persistence failed decision=%s", decision.decision_id)
    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.generate(request)
        yield response.content

    async def health_check(self) -> bool:
        route = self.registry.route_for_task("code_generation")
        provider = self._providers.get(route.provider_id)
        if provider is None:
            return False
        health = await provider.health()
        self.registry.connections.record_transport_result(route.provider_id, health)
        return health.ok

    def _candidate_provider_ids(
        self,
        preferred_provider: str,
        *,
        allow_fallback: bool,
        local_only: bool,
        task_type: str,
        fallback_provider_id: str | None,
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        if not local_only:
            ordered.append(preferred_provider)
        elif preferred_provider in self.registry.fallback_provider_ids(local_only=True):
            ordered.append(preferred_provider)
        if allow_fallback and fallback_provider_id and fallback_provider_id not in ordered:
            fallback = self.registry.provider(fallback_provider_id)
            primary = self.registry.provider(preferred_provider)
            if fallback.local == primary.local and (not local_only or fallback.local):
                ordered.append(fallback_provider_id)
        candidates: list[str] = []
        for provider_id in ordered:
            if provider_id not in self._providers:
                continue
            try:
                record = self.registry.connections.refresh_status(f"{provider_id}.default")
            except Exception:
                continue
            if not record.enabled or record.credential_status.value == "credential_missing":
                continue
            if record.auth_state.value == "authentication_failed":
                continue
            if record.transport_status.value in UNAVAILABLE_PROVIDER_HEALTH:
                continue
            candidates.append(provider_id)
        return tuple(candidates)

    def _record_failure(
        self,
        provider_id: str,
        model_id: str,
        task_type: str,
        exc: Exception,
        started: float,
    ) -> None:
        error_code = getattr(exc, "code", type(exc).__name__)
        self._record_usage(
            UsageRecord(
                provider_id=provider_id,
                model_id=model_id,
                task_type=task_type,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                success=False,
                error_code=str(error_code),
            )
        )

    def _record_usage(self, record: UsageRecord) -> None:
        try:
            self.usage.record(record)
        except OSError:
            # Telemetry must never replace the provider result or original
            # provider error with a filesystem failure.
            logger.exception("Could not persist model usage telemetry")

    def _to_llm_response(self, response: ModelResponse) -> LLMResponse:
        return LLMResponse(
            content=response.content,
            provider=PROVIDER_ENUMS.get(response.provider_id, LLMProvider.OPENROUTER),
            model=response.model_id,
            token_usage=response.token_usage(),
            latency_ms=response.latency_ms,
            provider_id=response.provider_id,
        )
