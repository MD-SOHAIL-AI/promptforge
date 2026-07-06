"""Model routing service used by ForgeX generation."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from ..services.llm_service import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMService,
)
from .models import ModelRequest, ModelResponse, UsageRecord
from .providers import (
    AnthropicProvider,
    BaseModelProvider,
    CodexAgentProvider,
    GeminiProvider,
    GroqProvider,
    LMStudioProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from .registry import ProviderRegistry
from .usage import UsageTracker


logger = logging.getLogger(__name__)


PROVIDER_ENUMS = {
    # The legacy LLM response contract has no CLI-agent enum. Keep that stable
    # while the model-router response still reports provider_id="codex".
    "codex": LLMProvider.OPENAI,
    "openrouter": LLMProvider.OPENROUTER,
    "openai": LLMProvider.OPENAI,
    "groq": LLMProvider.OPENAI,
    "anthropic": LLMProvider.ANTHROPIC,
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
    ) -> None:
        self.registry = registry or ProviderRegistry()
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
        self._providers = providers or {
            "codex": CodexAgentProvider(self.registry),
            "openrouter": OpenRouterProvider(self.registry),
            "openai": OpenAIProvider(self.registry),
            "groq": GroqProvider(self.registry),
            "gemini": GeminiProvider(self.registry),
            "anthropic": AnthropicProvider(self.registry),
            "lmstudio": LMStudioProvider(self.registry),
            "ollama": OllamaProvider(self.registry),
        }

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
                allow_fallback=allow_fallback if isinstance(allow_fallback, bool) else True,
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
        candidates = self._candidate_provider_ids(
            preferred_provider,
            allow_fallback=request.allow_fallback and route.fallback_enabled,
            local_only=request.local_only or route.local_only,
            task_type=request.task_type,
            fallback_provider_id=route.fallback_provider_id,
        )
        skip_provider = request.metadata.get("skip_provider_id")
        if isinstance(skip_provider, str) and skip_provider.strip():
            candidates = tuple(
                provider_id for provider_id in candidates if provider_id != skip_provider
            )
        if not candidates:
            preferred_meta = self.registry.provider(preferred_provider)
            if not preferred_meta.configured or not preferred_meta.enabled:
                raise LLMConfigurationError(
                    (
                        f"Model provider {preferred_provider} is not configured. "
                        f"Add your {preferred_meta.display_name} settings in Models."
                    ),
                    provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
                )
        started = time.monotonic()
        last_error: Exception | None = None
        for provider_id in candidates:
            provider_meta = self.registry.provider(provider_id)
            model_id = preferred_model if provider_id == preferred_provider else self.registry.default_model(provider_id)
            if not provider_meta.configured or not provider_meta.enabled:
                last_error = LLMConfigurationError(
                    (
                        f"Model provider {provider_id} is not configured. "
                        f"Add your {provider_meta.display_name} settings in Models."
                    ),
                    provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                )
                self._record_failure(provider_id, model_id, request.task_type, last_error, started)
                if provider_id == preferred_provider and not request.allow_fallback:
                    break
                continue
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
                response = await self._providers[provider_id].generate(routed_request)
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
                if provider_id == preferred_provider and not request.allow_fallback:
                    break
                continue
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
            if isinstance(last_error, LLMConfigurationError):
                raise last_error
            raise LLMProviderError(
                f"No model route available for {request.task_type}. Last error: {last_error}",
                provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
                retryable=True,
                details={"provider_id": preferred_provider},
            ) from last_error
        raise LLMConfigurationError(
            f"No model route available for {request.task_type}.",
            provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
            details={"task_type": request.task_type},
        )

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.generate(request)
        yield response.content

    async def health_check(self) -> bool:
        route = self.registry.route_for_task("code_generation")
        provider = self._providers.get(route.provider_id)
        if provider is None:
            return False
        health = await provider.health()
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
            if provider_id == "codex" and task_type != "code_generation":
                continue
            provider = self.registry.provider(provider_id)
            if provider_id != preferred_provider and (
                not provider.configured or not provider.enabled
            ):
                continue
            if (
                provider_id != preferred_provider
                and provider.health_status.strip().casefold() not in {"connected", "ready"}
            ):
                continue
            if provider.health_status.strip().casefold() in UNAVAILABLE_PROVIDER_HEALTH:
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
