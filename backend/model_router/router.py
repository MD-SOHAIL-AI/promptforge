"""Model routing service used by ForgeX generation."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from typing import Any

from backend.observability.diagnostics import emit_diagnostic_event

from ..services.llm_service import (
    LLMConfigurationError,
    LLMError,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMService,
)
from .models import CanonicalStreamEvent, ModelRequest, ModelResponse, UsageRecord
from .providers import (
    AnthropicProvider,
    BaseModelProvider,
    CerebrasProvider,
    DeepSeekProvider,
    CompatibleProvider,
    GeminiProvider,
    GroqProvider,
    LMStudioProvider,
    NvidiaNimProvider,
    OllamaProvider,
    OpenAIProvider,
    OpenRouterProvider,
)
from .registry import ProviderRegistry
from .usage import UsageTracker
from .catalog import ModelCatalog
from .resolver import ModelResolver
from .runtime import ModelRuntime
from .selection import ModelSelectionStore
from .admission import ProviderAdmissionController
from .circuit import ProviderCircuitBreaker

logger = logging.getLogger(__name__)


PROVIDER_ENUMS = {
    "deepseek": LLMProvider.OPENAI,
    "anthropic": LLMProvider.ANTHROPIC,
    "openrouter": LLMProvider.OPENROUTER,
    "openai": LLMProvider.OPENAI,
    "groq": LLMProvider.OPENAI,
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
    ) -> None:
        self.registry = registry or ProviderRegistry()
        self.usage = usage or UsageTracker()
        initial_route = self.registry.route_for_task("code_generation")
        # LLMService requires these compatibility attributes, but persisted route
        # state is authoritative. Request execution never mutates them.
        self.provider = PROVIDER_ENUMS.get(
            initial_route.provider_id, LLMProvider.OPENROUTER
        )
        self.model = initial_route.model_id
        self.timeout_s = 180.0
        if providers is None:
            from .providers.opencode import OpenCodeProvider
            self._providers = {provider_id: OpenCodeProvider(self.registry, provider_id)
                for provider_id in self.registry.provider_ids()}
        else:
            self._providers = providers
        self.catalog = ModelCatalog(self.registry)
        self.selections = ModelSelectionStore(self.registry.storage.path.with_suffix(".selections.sqlite3"))
        self.resolver = ModelResolver(self.registry, self.catalog, self.selections)
        self.runtime = ModelRuntime(self.resolver, self._providers, attempt_slot=self._physical_slot)
        self.admission = ProviderAdmissionController(path=self.registry.storage.path.with_suffix(".provider-admission.sqlite3"))
        self.circuit = ProviderCircuitBreaker(self.registry.storage.path.with_suffix(".provider-health.sqlite3"),
            failure_threshold=self.runtime.retry_policy.max_attempts if providers is None else 2)

    def _circuit_identity(self, provider_id: str, model_id: str) -> str:
        return self.circuit.identity(provider_id, model_id, self.registry.provider(provider_id).base_url,
            self.registry.api_key(provider_id))

    @asynccontextmanager
    async def _physical_slot(self, request):
        from .models import execution_value
        identity = self._circuit_identity(request.provider_id, request.model_id)
        # Record default-adapter failures too. Its threshold permits the normal
        # physical retry allowance before cooling subsequent logical turns.
        # Fail fast on cooling endpoints before joining a capacity queue.
        self.circuit.require_available(identity)
        async with self.admission.slot(request.provider_id, request.model_id,
            run_id=_metadata_str(request, "run_id"), turn_id=_metadata_str(request, "turn_id"),
            endpoint_identity=identity, deadline=execution_value(request, "deadline", "forgex_turn_deadline")):
            async with self.circuit.slot(identity):
                yield

    async def _physical_stream(self, request, stream):
        async with self._physical_slot(request):
            async with aclosing(stream(request)) as events:
                async for event in events:
                    yield event

    def install_custom_provider(self, provider_id: str) -> None:
        from .providers.opencode import OpenCodeProvider
        self.registry.definition(provider_id)
        self._providers[provider_id] = OpenCodeProvider(self.registry, provider_id)

    def remove_custom_provider(self, provider_id: str) -> None:
        if self.selections.references(provider_id):
            raise ValueError("Custom provider is referenced by routes or session/role selections")
        self.registry.delete_custom_provider(provider_id)
        self._providers.pop(provider_id, None)

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
                allow_fallback=(
                    allow_fallback if isinstance(allow_fallback, bool) else True
                ),
                local_only=local_only if isinstance(local_only, bool) else False,
                structured_output=None,
                metadata=dict(request.metadata),
            )
        )
        return self._to_llm_response(model_response)

    async def generate_model(self, request: ModelRequest) -> ModelResponse:
        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        resolved = self.resolver.resolve(request)
        started = time.monotonic()
        provider_request_id = _provider_request_id(request)
        emit_diagnostic_event(
            component="provider", event="provider.request.started",
            provider=resolved.provider_id, model=resolved.model_id,
            request_id=provider_request_id, run_id=_metadata_str(request, "run_id"),
            turn_id=_metadata_str(request, "turn_id"), session_id=_metadata_str(request, "session_id"),
            project_id=_metadata_str(request, "project_id"), task_type=request.task_type,
        )
        try:
            response = await self.runtime.generate(request, resolved=resolved)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_failure(resolved.provider_id, resolved.model_id, request.task_type, exc, started)
            emit_diagnostic_event(
                component="provider", event="provider.request.failed", level="ERROR",
                provider=resolved.provider_id, model=resolved.model_id,
                request_id=provider_request_id, run_id=_metadata_str(request, "run_id"),
                turn_id=_metadata_str(request, "turn_id"), session_id=_metadata_str(request, "session_id"),
                project_id=_metadata_str(request, "project_id"), task_type=request.task_type,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=str(getattr(exc, "code", type(exc).__name__)),
                message=str(exc)[:1024], status=getattr(exc, "status_code", None),
            )
            raise
        self._record_usage(UsageRecord(
            provider_id=response.provider_id, model_id=response.model_id, task_type=request.task_type,
            latency_ms=response.latency_ms, success=True, input_tokens=response.input_tokens,
            output_tokens=response.output_tokens, total_tokens=response.total_tokens,
            estimated_cost=response.estimated_cost,
        ))
        emit_diagnostic_event(
            component="provider", event="provider.request.completed", provider=response.provider_id,
            model=response.model_id, request_id=provider_request_id, run_id=_metadata_str(request, "run_id"),
            turn_id=_metadata_str(request, "turn_id"), session_id=_metadata_str(request, "session_id"),
            project_id=_metadata_str(request, "project_id"), task_type=request.task_type, status=200,
            duration_ms=response.latency_ms or max(0, int((time.monotonic() - started) * 1000)),
            input_tokens=response.input_tokens, output_tokens=response.output_tokens, total_tokens=response.total_tokens,
        )
        return response

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        task_type = str(request.metadata.get("task_type", "code_generation"))
        route = self.registry.route_for_task(task_type)
        provider_id = request.metadata.get("provider_id")
        selected_provider = (
            provider_id
            if isinstance(provider_id, str) and provider_id
            else route.provider_id
        )
        selected_model = (
            request.metadata.get("model_id")
            if isinstance(request.metadata.get("model_id"), str)
            else route.model_id
        )
        routed = ModelRequest(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            task_type=task_type,
            provider_id=selected_provider,
            model_id=selected_model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            allow_fallback=False,
            local_only=bool(request.metadata.get("local_only", False))
            or route.local_only,
            structured_output=None,
            metadata=dict(request.metadata),
        )
        async for event in self.stream_model(routed):
            if event.type == "text_delta" and event.text:
                yield event.text

    async def stream_model(
        self, request: ModelRequest
    ) -> AsyncIterator[CanonicalStreamEvent]:
        """Stream canonical events from one explicitly selected model route."""

        resolved = self.resolver.resolve(request, streaming=True)
        provider_id = resolved.provider_id
        model_id = resolved.model_id
        adapter = self._providers.get(provider_id)
        stream = getattr(adapter, "generate_event_stream", None) if adapter else None
        if not callable(stream):
            raise LLMProviderError(
                f"Model provider {provider_id} does not support canonical streaming.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
                code="PROVIDER_STREAM_UNSUPPORTED",
                retryable=False,
                details={
                    "provider_id": provider_id,
                    "streaming_supported": False,
                },
            )
        provider = self.registry.provider(provider_id)
        if not provider.configured or not provider.enabled:
            raise LLMConfigurationError(
                "The selected streaming provider is unavailable for this route.",
                provider=PROVIDER_ENUMS.get(provider_id, LLMProvider.OPENROUTER),
            )
        routed = ModelRequest(
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            task_type=request.task_type,
            provider_id=provider_id,
            model_id=model_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            allow_fallback=False,
            local_only=request.local_only,
            structured_output=request.structured_output,
            metadata=request.metadata,
            execution_context=request.execution_context,
        )
        started = time.monotonic()
        provider_request_id = _provider_request_id(request)
        success = False
        error_code: str | None = None
        token_usage: dict[str, int] = {}
        emit_diagnostic_event(
            component="provider",
            event="provider.request.started",
            provider=provider_id,
            model=model_id,
            request_id=provider_request_id,
            run_id=_metadata_str(request, "run_id"),
            turn_id=_metadata_str(request, "turn_id"),
            session_id=_metadata_str(request, "session_id"),
            project_id=_metadata_str(request, "project_id"),
            task_type=request.task_type,
            streaming=True,
        )
        try:
            from .request_recovery import managed, agent_stream, public_stream
            physical = lambda value: self._physical_stream(value, stream)
            event_stream = agent_stream(routed, physical) if managed(routed) else public_stream(routed, physical)
            async with aclosing(event_stream) as events:
                async for event in events:
                    token_usage.update(event.usage)
                    yield event
            success = True
        except asyncio.CancelledError:
            error_code = "CANCELLED"
            emit_diagnostic_event(
                component="provider",
                event="provider.request.failed",
                level="WARNING",
                provider=provider_id,
                model=model_id,
                request_id=provider_request_id,
                run_id=_metadata_str(request, "run_id"),
                turn_id=_metadata_str(request, "turn_id"),
                session_id=_metadata_str(request, "session_id"),
                project_id=_metadata_str(request, "project_id"),
                task_type=request.task_type,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=error_code,
                streaming=True,
            )
            raise
        except Exception as exc:
            error_code = str(getattr(exc, "code", type(exc).__name__))
            emit_diagnostic_event(
                component="provider",
                event="provider.request.failed",
                level="ERROR",
                provider=provider_id,
                model=model_id,
                request_id=provider_request_id,
                run_id=_metadata_str(request, "run_id"),
                turn_id=_metadata_str(request, "turn_id"),
                session_id=_metadata_str(request, "session_id"),
                project_id=_metadata_str(request, "project_id"),
                task_type=request.task_type,
                duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                error_code=error_code,
                message=str(exc)[:1024],
                status=getattr(exc, "status_code", None),
                retryable=bool(getattr(exc, "retryable", False)),
                streaming=True,
            )
            raise
        finally:
            self._record_usage(UsageRecord(
                provider_id=provider_id,
                model_id=model_id,
                task_type=request.task_type,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                success=success,
                error_code=error_code,
                input_tokens=token_usage.get("input_tokens"),
                output_tokens=token_usage.get("output_tokens"),
                total_tokens=token_usage.get("total_tokens"),
            ))
        emit_diagnostic_event(
            component="provider",
            event="provider.request.completed",
            provider=provider_id,
            model=model_id,
            request_id=provider_request_id,
            run_id=_metadata_str(request, "run_id"),
            turn_id=_metadata_str(request, "turn_id"),
            session_id=_metadata_str(request, "session_id"),
            project_id=_metadata_str(request, "project_id"),
            task_type=request.task_type,
            status=200,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            input_tokens=token_usage.get("input_tokens"),
            output_tokens=token_usage.get("output_tokens"),
            total_tokens=token_usage.get("total_tokens"),
            streaming=True,
        )

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
        del allow_fallback, task_type, fallback_provider_id
        provider = self.registry.provider(preferred_provider)
        if local_only and not provider.local:
            return ()
        return (preferred_provider,) if preferred_provider in self._providers else ()

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
        except (OSError, sqlite3.Error):
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
            structured_output=response.structured_output,
            tool_calls=response.tool_calls,
            finish_reason=response.finish_reason,
            request_id=response.request_id,
            reasoning=response.reasoning,
            truncated=response.truncated,
        )


def _metadata_str(request: ModelRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _provider_request_id(request: ModelRequest) -> str:
    explicit = _metadata_str(request, "request_id")
    if explicit:
        return explicit
    http_request_id = _active_http_request_id()
    if http_request_id:
        return f"{http_request_id}:provider"
    return f"provider-{uuid.uuid4().hex}"


def _active_http_request_id() -> str:
    try:
        from backend.api.observability import request_id_context

        return request_id_context.get()
    except Exception:
        return ""
