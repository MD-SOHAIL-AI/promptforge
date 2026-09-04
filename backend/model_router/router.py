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
    BaseModelProvider,
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
    "openrouter": LLMProvider.OPENROUTER,
    "openai": LLMProvider.OPENAI,
    "groq": LLMProvider.OPENAI,
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
        # LLMService requires these compatibility attributes, but persisted route
        # state is authoritative. Request execution never mutates them.
        self.provider = PROVIDER_ENUMS.get(initial_route.provider_id, LLMProvider.OPENROUTER)
        self.model = initial_route.model_id
        self.timeout_s = 180.0
        self._providers = providers or {
            "openrouter": OpenRouterProvider(self.registry),
            "openai": OpenAIProvider(self.registry),
            "groq": GroqProvider(self.registry),
            "gemini": GeminiProvider(self.registry),
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
        preferred_provider = request.provider_id or route.provider_id
        preferred_model = request.model_id or (
            route.model_id if preferred_provider == route.provider_id else self.registry.default_model(preferred_provider)
        )
        requested_fallback = request.metadata.get("fallback_provider_id")
        fallback_provider_id = requested_fallback if isinstance(requested_fallback, str) and requested_fallback.strip() else route.fallback_provider_id
        fallback_enabled = route.fallback_enabled or (
            isinstance(requested_fallback, str) and bool(requested_fallback.strip())
        )
        candidates = self._candidate_provider_ids(
            preferred_provider,
            allow_fallback=request.allow_fallback and fallback_enabled,
            local_only=request.local_only or route.local_only,
            task_type=request.task_type,
            fallback_provider_id=fallback_provider_id,
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
            return response
        if last_error is not None:
            if isinstance(last_error, LLMConfigurationError):
                raise last_error
            retryable = bool(getattr(last_error, "retryable", False))
            raise LLMProviderError(
                f"No model route available for {request.task_type}. Last error: {last_error}",
                provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
                retryable=retryable,
                details={"provider_id": preferred_provider},
            ) from last_error
        raise LLMConfigurationError(
            f"No model route available for {request.task_type}.",
            provider=PROVIDER_ENUMS.get(preferred_provider, LLMProvider.OPENROUTER),
            details={"task_type": request.task_type},
        )

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        task_type = str(request.metadata.get("task_type", "code_generation"))
        route = self.registry.route_for_task(task_type)
        provider_id = request.metadata.get("provider_id")
        selected_provider = provider_id if isinstance(provider_id, str) and provider_id else route.provider_id
        adapter = self._providers.get(selected_provider)
        stream = getattr(adapter, "generate_stream", None) if adapter is not None else None
        if not callable(stream):
            raise LLMProviderError(
                f"Model provider {selected_provider} does not support streaming.",
                provider=PROVIDER_ENUMS.get(selected_provider, LLMProvider.OPENROUTER),
                retryable=False,
                details={"provider_id": selected_provider, "streaming_supported": False},
            )
        async for chunk in stream(
            ModelRequest(
                prompt=request.prompt,
                system_prompt=request.system_prompt,
                task_type=task_type,  # type: ignore[arg-type]
                provider_id=selected_provider,
                model_id=request.metadata.get("model_id") if isinstance(request.metadata.get("model_id"), str) else route.model_id,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                allow_fallback=False,
                local_only=bool(request.metadata.get("local_only", False)),
                metadata=dict(request.metadata),
            )
        ):
            yield chunk

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
        del task_type  # capability filtering belongs in provider metadata, not hard-coded IDs.
        ordered: list[str] = []
        preferred = self.registry.provider(preferred_provider)
        if not local_only or preferred.local:
            ordered.append(preferred_provider)

        if allow_fallback:
            fallbacks: list[str] = []
            if fallback_provider_id:
                fallbacks.append(fallback_provider_id)
            for provider_id in self.registry.fallback_provider_ids(local_only=local_only):
                if provider_id not in fallbacks:
                    fallbacks.append(provider_id)
            for provider_id in fallbacks:
                if provider_id in ordered or provider_id not in self._providers:
                    continue
                provider = self.registry.provider(provider_id)
                if local_only and not provider.local:
                    continue
                # Do not silently switch between local and cloud categories.
                if provider.local != preferred.local:
                    continue
                ordered.append(provider_id)

        candidates: list[str] = []
        for provider_id in ordered:
            if provider_id not in self._providers:
                continue
            provider = self.registry.provider(provider_id)
            if provider_id != preferred_provider and (not provider.configured or not provider.enabled):
                continue
            if provider.health_status.strip().casefold() in UNAVAILABLE_PROVIDER_HEALTH:
                continue
            # Unknown/stale health is not a reason to suppress a configured
            # fallback; the request itself is the live probe.
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
