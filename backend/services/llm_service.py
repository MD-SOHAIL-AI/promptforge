"""Unified async LLM provider interface for PromptForge AI.

This module owns provider communication only. It contains no planning,
coordination, prompt construction policy, or other application business logic.
Provider integrations use documented HTTP APIs through ``httpx`` so callers
can inject a client or transport for testing and deployment-specific control.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncio
from email.utils import parsedate_to_datetime
import json
import math
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, ClassVar
from contextlib import aclosing
import uuid
import inspect

import httpx

if TYPE_CHECKING:
    from ..model_router.models import CanonicalStreamEvent

from ..core.config import DEFAULT_LLM_TIMEOUT_SECONDS
from ..contracts.transport import TransportPolicy
from ..utils.serialization import (
    _freeze_json_value,
    _freeze_mapping as _freeze_mapping_value,
)

__all__ = [
    "AnthropicService",
    "DEFAULT_LLM_TIMEOUT_SECONDS",
    "GeminiService",
    "LLMAuthenticationError",
    "LLMConfigurationError",
    "LLMError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseError",
    "LLMService",
    "LLMTimeoutError",
    "OpenAIService",
    "OpenRouterService",
]


@unique
class LLMProvider(str, Enum):
    """LLM providers supported by the unified service contract."""

    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    GEMINI = "GEMINI"
    OPENROUTER = "OPENROUTER"
    OLLAMA = "OLLAMA"
    LMSTUDIO = "LMSTUDIO"


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Provider-neutral request for one model generation."""

    prompt: str
    system_prompt: str | None = None
    temperature: float = 0.7
    max_tokens: int = 1024
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_text(self.prompt, field_name="prompt")
        if self.system_prompt is not None:
            _validate_text(self.system_prompt, field_name="system_prompt")
        if (
            not isinstance(self.temperature, (int, float))
            or isinstance(self.temperature, bool)
            or not math.isfinite(float(self.temperature))
            or not 0.0 <= float(self.temperature) <= 2.0
        ):
            raise ValueError("temperature must be between 0.0 and 2.0")
        if (
            not isinstance(self.max_tokens, int)
            or isinstance(self.max_tokens, bool)
            or self.max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")

        object.__setattr__(self, "temperature", float(self.temperature))
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, field_name="metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible representation of the request."""

        return {
            "prompt": self.prompt,
            "system_prompt": self.system_prompt,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LLMRequest:
        """Build a request from its exact canonical dictionary schema."""

        values = _validate_schema(
            data,
            expected={
                "prompt",
                "system_prompt",
                "temperature",
                "max_tokens",
                "metadata",
            },
            label="LLM request",
        )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized result returned by any supported provider."""

    content: str
    provider: LLMProvider
    model: str
    token_usage: Mapping[str, int] = field(default_factory=dict, hash=False)
    latency_ms: int = 0
    provider_id: str | None = None
    structured_output: Mapping[str, Any] | None = field(default=None, hash=False)
    tool_calls: tuple[Mapping[str, Any], ...] = field(default=(), hash=False)
    finish_reason: str | None = None
    request_id: str | None = None
    reasoning: str | None = None
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or "\x00" in self.content:
            raise ValueError("content must be a string without NUL characters")
        if (
            not self.content.strip()
            and self.structured_output is None
            and not self.tool_calls
        ):
            raise ValueError("content must be non-empty when no structured output exists")
        if not isinstance(self.provider, LLMProvider):
            raise ValueError("provider must be an LLMProvider")
        if self.provider_id is not None:
            _validate_text(self.provider_id, field_name="provider_id")
        _validate_text(self.model, field_name="model")
        if (
            not isinstance(self.latency_ms, int)
            or isinstance(self.latency_ms, bool)
            or self.latency_ms < 0
        ):
            raise ValueError("latency_ms must be a non-negative integer")

        if not isinstance(self.token_usage, Mapping):
            raise ValueError("token_usage must be a mapping")
        usage: dict[str, int] = {}
        for key, value in self.token_usage.items():
            if not isinstance(key, str) or not key:
                raise ValueError("token_usage keys must be non-empty strings")
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("token_usage values must be non-negative integers")
            usage[key] = value
        object.__setattr__(self, "token_usage", MappingProxyType(usage))

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible representation of the response."""

        return {
            "content": self.content,
            "provider": self.provider.value,
            "model": self.model,
            "token_usage": dict(self.token_usage),
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LLMResponse:
        """Build a response from its exact canonical dictionary schema."""

        values = _validate_schema(
            data,
            expected={
                "content",
                "provider",
                "model",
                "token_usage",
                "latency_ms",
            },
            label="LLM response",
        )
        try:
            provider = LLMProvider(values["provider"])
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(item.value for item in LLMProvider)
            raise ValueError(f"provider must be one of: {allowed}") from exc
        values["provider"] = provider
        return cls(**values)


class LLMError(RuntimeError):
    """Base structured error raised by all LLM services."""

    default_code: ClassVar[str] = "LLM_ERROR"

    def __init__(
        self,
        message: str,
        *,
        provider: LLMProvider,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.code = code or self.default_code
        self.status_code = status_code
        self.retryable = retryable
        self.details = _freeze_mapping(
            details or {},
            field_name="details",
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible error payload for logs or APIs."""

        return {
            "error": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "provider": self.provider.value,
            "status_code": self.status_code,
            "retryable": self.retryable,
            "details": _thaw_json_value(self.details),
        }


class LLMConfigurationError(LLMError):
    """Provider configuration is missing or invalid."""

    default_code = "CONFIGURATION_ERROR"


class LLMTimeoutError(LLMError):
    """A provider operation exceeded its configured deadline."""

    default_code = "PROVIDER_TIMEOUT"


class LLMAuthenticationError(LLMError):
    """The provider rejected supplied authentication credentials."""

    default_code = "PROVIDER_AUTH_ERROR"


class LLMRateLimitError(LLMError):
    """The provider rejected a request due to quota or rate limits."""

    default_code = "PROVIDER_RATE_LIMITED"


class LLMProviderError(LLMError):
    """The provider returned a non-success response."""

    default_code = "PROVIDER_HTTP_ERROR"


class LLMResponseError(LLMError):
    """A successful provider response had an invalid or empty shape."""

    default_code = "PROVIDER_ENVELOPE_INVALID"


class LLMService(ABC):
    """Abstract async interface implemented by every LLM provider."""

    provider: LLMProvider
    model: str
    timeout_s: float

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate one complete response."""

    @abstractmethod
    def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """Yield text deltas as the provider streams them."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return whether the provider API is reachable and authenticated."""


class _HTTPService(LLMService, ABC):
    """Shared HTTP lifecycle, timeout, and error behavior."""

    provider: LLMProvider
    default_base_url: ClassVar[str]

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        try:
            _validate_text(api_key, field_name="api_key")
            _validate_text(model, field_name="model")
        except ValueError as exc:
            raise LLMConfigurationError(
                str(exc),
                provider=self.provider,
            ) from exc
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or timeout_s <= 0
        ):
            raise LLMConfigurationError(
                "timeout_s must be a positive finite number",
                provider=self.provider,
            )
        if client is not None and not isinstance(client, httpx.AsyncClient):
            raise LLMConfigurationError(
                "client must be an httpx.AsyncClient",
                provider=self.provider,
            )

        self.api_key = api_key
        self.model = model
        self.timeout_s = float(timeout_s)
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s)
        )
        self._last_request_metadata: dict[str, Any] = {
            "outbound_request_count": 0,
            "request_reached_provider": False,
            "http_status": None,
            "provider_request_id": None,
            "retry_after_ms": None,
        }
        self._execution_context = None
        self._transport_policy: TransportPolicy | None = None
        self._provider_name = self.provider.value
        self._request_started = 0.0
        self._request_identity = {}
        self._last_progress_report = 0.0
        self._attempt_bound_by_parent = False

    def bind_execution_context(self, context, *, provider_id: str, policy: TransportPolicy | None = None, identity=None) -> None:
        self._execution_context = context
        self._provider_name = provider_id
        self._transport_policy = getattr(context, "transport_policy", None) or policy
        self._request_identity = dict(identity or {})

    def _start_request(self) -> None:
        self._request_started = time.monotonic()
        self._last_progress_report = 0.0
        diagnostics = getattr(self._execution_context, "diagnostics", {})
        self._last_request_metadata = {
            "outbound_request_count": 1, "request_reached_provider": False,
            "http_status": None, "provider_request_id": None, "retry_after_ms": None,
            "physical_attempt_id": diagnostics.get("physical_attempt_id") or uuid.uuid4().hex,
            "response_headers_received": False, "first_event_ms": None,
            "last_event_ms": None, "stream_event_count": 0,
            "completion_received": False, "timeout_stage": None,
        }
        self._transport_event("started")

    def _transport_event(self, phase: str) -> None:
        context = self._execution_context
        if context is None:
            return
        values = self.to_safe_metadata()
        values["duration_ms"] = _elapsed_ms(self._request_started)
        context.diagnostics.update(values)
        from backend.observability.diagnostics import emit_diagnostic_event
        emit_diagnostic_event(component="provider", event="provider.attempt." + phase,
                              provider_id=self._provider_name, model_id=self.model,
                              **self._request_identity,
                              **{k: v for k, v in values.items() if k != "outbound_request_count"})

    async def _report_progress(self, *, first: bool) -> None:
        now = time.monotonic()
        if not first and now - self._last_progress_report < 2.0:
            return
        self._last_progress_report = now
        self._transport_event("progress")
        hook = getattr(self._execution_context, "recovery_hook", None)
        if callable(hook):
            values = self.to_safe_metadata()
            payload = {"event_type": "model.request.progress", "provider_id": self._provider_name,
                       "model_id": self.model, **self._request_identity,
                       **{key: values[key] for key in ("physical_attempt_id", "response_headers_received", "request_reached_provider", "http_status", "first_event_ms", "last_event_ms", "stream_event_count")}}
            # Progress diagnostics must not change the execution result.
            try:
                result = hook(payload)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass

    def _attempt_timeout(self, request: LLMRequest) -> float | None:
        total = self._transport_policy.total_seconds if self._transport_policy else _generation_timeout(request, self.timeout_s)
        deadline = getattr(self._execution_context, "deadline", None)
        self._attempt_bound_by_parent = False
        if deadline is not None:
            remaining = max(0.0, deadline - time.monotonic())
            self._attempt_bound_by_parent = total is None or remaining <= total
            return min(total, remaining) if total is not None else remaining
        return total

    def _http_timeout(self, total: float | None):
        policy = self._transport_policy
        if policy is None:
            return total
        def bounded(value):
            return min(value, max(0.001, total)) if total is not None else value
        return httpx.Timeout(connect=bounded(policy.connect_seconds),
                             read=bounded(max(policy.first_event_seconds, policy.idle_seconds)),
                             write=bounded(policy.write_seconds), pool=bounded(policy.pool_seconds))

    def to_safe_metadata(self) -> dict[str, Any]:
        return dict(self._last_request_metadata)

    async def __aenter__(self) -> _HTTPService:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the internally-created HTTP client, if any."""

        if self._owns_client:
            await self._client.aclose()

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        started = time.monotonic()
        self._start_request()
        request_timeout = self._attempt_timeout(request)
        try:
            async with _timeout_after(request_timeout):
                response = await self._client.post(
                    self._generation_url(stream=False),
                    headers=self._headers(),
                    params=self._params(stream=False),
                    json=self._payload(request, stream=False),
                    timeout=self._http_timeout(request_timeout),
                )
                self._capture_response_metadata(response)
                self._raise_for_status(response)
                data = self._decode_json(response)
                self._raise_embedded_provider_error(data)
                from backend.model_router.normalization import (
                    ProviderNormalizationError,
                    normalize_response,
                )

                try:
                    canonical = normalize_response(
                        data,
                        provider_id=self.provider.value.casefold(),
                        model_id=self.model,
                    )
                except ProviderNormalizationError as exc:
                    raise LLMResponseError(
                        f"{self.provider.value} returned an invalid response: {exc}",
                        provider=self.provider,
                        code=exc.code,
                    ) from exc
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise self._timeout_error("generation", exc=exc) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"{self.provider.value} request failed: {exc}",
                provider=self.provider,
                retryable=True,
                details={
                    "exception_type": type(exc).__name__,
                    **self.to_safe_metadata(),
                },
            ) from exc

        self._last_request_metadata["completion_received"] = True
        self._transport_event("completed")
        return LLMResponse(
            content=canonical.text or "",
            provider=self.provider,
            model=canonical.model_id or self.model,
            token_usage=canonical.usage,
            latency_ms=_elapsed_ms(started),
            provider_id=self.provider.value.casefold(),
            structured_output=canonical.structured_output,
            tool_calls=canonical.tool_calls,
            finish_reason=canonical.finish_reason,
            request_id=(
                canonical.request_id
                or self.to_safe_metadata().get("provider_request_id")
            ),
            reasoning=canonical.reasoning,
            truncated=canonical.truncated,
        )

    async def generate_stream(
        self,
        request: LLMRequest,
    ) -> AsyncIterator[str]:
        async for event in self.generate_event_stream(request):
            if event.type == "text_delta" and event.text:
                yield event.text

    async def generate_event_stream(
        self,
        request: LLMRequest,
    ) -> AsyncIterator[CanonicalStreamEvent]:
        """Yield normalized events; raw provider packets never escape."""

        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        self._start_request()
        request_timeout = self._attempt_timeout(request)
        policy = self._transport_policy
        progress_timeout = policy.first_event_seconds if policy else request_timeout
        idle_timeout = policy.idle_seconds if policy else request_timeout
        watchdog = _Deadline(progress_timeout)
        overall = _Deadline(request_timeout)
        terminal_event: CanonicalStreamEvent | None = None
        try:
            async with overall, watchdog:
                async with self._client.stream(
                    "POST",
                    self._generation_url(stream=True),
                    headers=self._headers(),
                    params=self._params(stream=True),
                    json=self._payload(request, stream=True),
                    timeout=self._http_timeout(request_timeout),
                ) as response:
                    self._capture_response_metadata(response)
                    # Streaming responses are not pre-buffered by httpx. Read
                    # an error response before the synchronous status decoder
                    # inspects its JSON/text body; otherwise httpx raises
                    # ResponseNotRead and hides the real upstream status.
                    if not response.is_success:
                        await response.aread()
                    self._raise_for_status(response)
                    async with aclosing(_iter_sse_json(response, provider=self.provider)) as packets:
                        async for data in packets:
                            self._raise_embedded_provider_error(data)
                            from backend.model_router.normalization import ProviderNormalizationError, normalize_stream_event
                            try:
                                events = normalize_stream_event(data, provider_id=self.provider.value.casefold())
                            except ProviderNormalizationError as exc:
                                raise LLMResponseError(str(exc), provider=self.provider, code=exc.code) from exc
                            for event in events:
                                if event.type == "error":
                                    raise LLMResponseError("Provider returned an invalid stream event.",
                                        provider=self.provider, code=event.error_code or "PROVIDER_STREAM_INVALID")
                                meaningful = bool(event.text or event.arguments_delta or event.arguments or event.tool_call or event.type == "done")
                                if meaningful:
                                    elapsed = _elapsed_ms(self._request_started)
                                    first = self._last_request_metadata["first_event_ms"] is None
                                    self._last_request_metadata["first_event_ms"] = self._last_request_metadata["first_event_ms"] if not first else elapsed
                                    self._last_request_metadata["last_event_ms"] = elapsed
                                    self._last_request_metadata["stream_event_count"] += 1
                                    watchdog.reset(idle_timeout)
                                    if first:
                                        self._transport_event("first_event")
                                    await self._report_progress(first=first)
                                if event.type == "done":
                                    terminal_event = _merge_terminal_stream_event(terminal_event, event)
                                else:
                                    yield event
                            # These markers end a protocol response; finish_reason alone
                            # may still be followed by a usage packet.
                            if data.get("type") in {"forgex.stream.done", "response.completed", "response.done", "message_stop"}:
                                break
                if terminal_event is not None:
                    self._last_request_metadata["completion_received"] = True
                    self._transport_event("completed")
                    yield terminal_event
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            stage = "stream_idle" if self._last_request_metadata["first_event_ms"] is not None else "first_event"
            if overall._expired:
                stage = getattr(self._execution_context, "deadline_kind", "job_deadline") if self._attempt_bound_by_parent else "attempt_total"
            raise self._timeout_error("streaming generation", exc=exc, stage=stage) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"{self.provider.value} streaming request failed: {exc}",
                provider=self.provider,
                retryable=True,
                details={"exception_type": type(exc).__name__, **self.to_safe_metadata()},
            ) from exc

    async def health_check(self) -> bool:
        try:
            async with _timeout_after(self.timeout_s):
                response = await self._client.get(
                    self._health_url(),
                    headers=self._headers(),
                    params=self._health_params(),
                    timeout=self.timeout_s,
                )
                self._raise_for_status(response)
                return True
        except asyncio.CancelledError:
            raise
        except (LLMError, TimeoutError, httpx.HTTPError):
            return False

    def _timeout_error(self, operation: str, *, exc=None, stage: str = "attempt_total") -> LLMTimeoutError:
        deadline = getattr(self._execution_context, "deadline", None)
        if (deadline is not None and deadline <= time.monotonic()
                or stage == "attempt_total" and self._attempt_bound_by_parent):
            stage = getattr(self._execution_context, "deadline_kind", "job_deadline")
        for exception_type, name in ((httpx.ConnectTimeout, "connect"), (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool")):
            if stage != "job_deadline" and isinstance(exc, exception_type):
                stage = name
        self._last_request_metadata["timeout_stage"] = stage
        self._last_request_metadata["duration_ms"] = _elapsed_ms(self._request_started)
        self._transport_event("failed")
        return LLMTimeoutError(
            f"{self._provider_name} {operation} timed out during {stage} after "
            f"{self._last_request_metadata['duration_ms'] / 1000:g} seconds",
            provider=self.provider,
            code="AGENT_RUNTIME_LIMIT_EXCEEDED" if stage == "job_deadline" else "AGENT_TURN_LIMIT_EXCEEDED" if stage == "turn_deadline" else "PROVIDER_TIMEOUT",
            retryable=stage not in {"job_deadline", "turn_deadline"},
            details={"timeout_s": self.timeout_s, "operation": operation,
                     "origin": stage if stage in {"job_deadline", "turn_deadline"} else "provider_transport", **self.to_safe_metadata()},
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        message, code, details = _provider_error_body(response)
        common = {
            "provider": self.provider,
            "status_code": response.status_code,
            "details": {
                **details,
                **({"upstream_error_code": code} if code else {}),
                **self.to_safe_metadata(),
            },
        }
        if response.status_code in {401, 403}:
            raise LLMAuthenticationError(message, code="PROVIDER_AUTH_ERROR", **common)
        if response.status_code == 429:
            raise LLMRateLimitError(
                message,
                code="PROVIDER_RATE_LIMITED",
                retryable=True,
                **common,
            )
        raise LLMProviderError(
            message,
            code=(
                "PROVIDER_UNAVAILABLE"
                if response.status_code >= 500
                else "PROVIDER_HTTP_ERROR"
            ),
            retryable=response.status_code >= 500,
            **common,
        )

    def _capture_response_metadata(self, response: httpx.Response) -> None:
        request_id = response.headers.get("x-request-id") or response.headers.get(
            "cf-ray"
        )
        if request_id and (
            len(request_id) > 128
            or not all(
                character.isalnum() or character in "._:-" for character in request_id
            )
        ):
            request_id = None
        retry_after_ms = _retry_after_ms(response.headers.get("retry-after") or response.headers.get("retry-after-ms"), milliseconds=response.headers.get("retry-after-ms") is not None)
        self._last_request_metadata.update(
            response_headers_received=True,
            request_reached_provider=True,
            http_status=response.status_code,
            provider_request_id=request_id,
            retry_after_ms=retry_after_ms,
        )
        self._transport_event("headers")

    def _decode_json(self, response: httpx.Response) -> Mapping[str, Any]:
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMResponseError(
                f"{self.provider.value} returned invalid JSON",
                provider=self.provider,
                details={"body": response.text[:1000]},
            ) from exc
        if not isinstance(data, Mapping):
            raise LLMResponseError(
                f"{self.provider.value} returned a non-object response",
                provider=self.provider,
            )
        return data

    def _params(self, *, stream: bool) -> Mapping[str, str]:
        del stream
        return {}

    def _health_params(self) -> Mapping[str, str]:
        return self._params(stream=False)

    def _raise_embedded_provider_error(self, data: Mapping[str, Any]) -> None:
        """Classify HTTP-200 provider errors before content normalization."""

        error = data.get("error")
        if not error:
            return
        message = "Provider returned an error"
        code: str | None = None
        details: dict[str, Any] = {}
        if isinstance(error, Mapping):
            raw_message = error.get("message")
            raw_code = error.get("code") or error.get("type")
            if isinstance(raw_message, str) and raw_message.strip():
                message = raw_message.strip()
            if (
                isinstance(raw_code, (str, int))
                and not isinstance(raw_code, bool)
                and str(raw_code).strip()
            ):
                code = str(raw_code).strip()
                details["provider_error_code"] = code[:128]
        elif isinstance(error, str) and error.strip():
            message = error.strip()
        numeric_status = _embedded_provider_status(code)
        combined = f"{code or ''} {message}".casefold()
        common = {
            "provider": self.provider,
            "status_code": numeric_status,
            "details": {**details, **self.to_safe_metadata()},
        }
        if numeric_status in {401, 403} or any(
            marker in combined for marker in ("invalid api key", "unauthorized", "authentication")
        ):
            raise LLMAuthenticationError(
                message, code="PROVIDER_AUTH_ERROR", retryable=False, **common
            )
        if numeric_status == 429 or "rate_limit" in combined or "rate limit" in combined:
            raise LLMRateLimitError(
                message, code="PROVIDER_RATE_LIMITED", retryable=True, **common
            )
        if any(
            marker in combined
            for marker in (
                "no endpoints found that support",
                "unsupported parameter",
                "does not support tool",
            )
        ):
            raise LLMProviderError(
                message,
                code="MODEL_CAPABILITY_UNSUPPORTED",
                retryable=False,
                **common,
            )
        unavailable = (
            numeric_status is not None and numeric_status >= 500
        ) or any(
            marker in combined
            for marker in ("provider_unavailable", "provider unavailable", "upstream unavailable")
        )
        raise LLMProviderError(
            message,
            code="PROVIDER_UNAVAILABLE" if unavailable else (code or "PROVIDER_HTTP_ERROR"),
            retryable=unavailable or _provider_error_message_is_retryable(message),
            **common,
        )

    def _parse_stream_event(self, data: Mapping[str, Any]) -> tuple[str, ...]:
        """Compatibility wrapper over the single canonical stream parser."""

        self._raise_embedded_provider_error(data)
        from backend.model_router.normalization import (
            ProviderNormalizationError,
            normalize_stream_event,
        )

        try:
            events = normalize_stream_event(
                data, provider_id=self.provider.value.casefold()
            )
        except ProviderNormalizationError as exc:
            raise LLMResponseError(
                str(exc), provider=self.provider, code=exc.code
            ) from exc
        error = next((event for event in events if event.type == "error"), None)
        if error is not None:
            raise LLMResponseError(
                "Provider returned an invalid stream event.",
                provider=self.provider,
                code=error.error_code or "PROVIDER_STREAM_INVALID",
            )
        # Raw reasoning stays out of the legacy public-text iterator.
        return tuple(
            event.text or "" for event in events if event.type == "text_delta"
        )

    @abstractmethod
    def _headers(self) -> Mapping[str, str]:
        raise NotImplementedError

    @abstractmethod
    def _generation_url(self, *, stream: bool) -> str:
        raise NotImplementedError

    @abstractmethod
    def _health_url(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        raise NotImplementedError

class _OpenAICompatibleService(_HTTPService, ABC):
    """Shared adapter for OpenAI-compatible chat completion APIs."""

    def _headers(self) -> Mapping[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _generation_url(self, *, stream: bool) -> str:
        del stream
        return f"{self.base_url}/chat/completions"

    def _health_url(self) -> str:
        return f"{self.base_url}/models"

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system_prompt is not None:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(_openai_canonical_messages(request.prompt, request.metadata))
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        _apply_openai_structured_output(payload, request.metadata)
        return payload

class OpenAIService(_OpenAICompatibleService):
    """OpenAI chat-completion provider implementation."""

    provider = LLMProvider.OPENAI
    default_base_url = "https://api.openai.com/v1"


class OpenAIResponsesService(_OpenAICompatibleService):
    """OpenAI Responses API implementation for custom provider routes."""

    provider = LLMProvider.OPENAI
    default_base_url = "https://api.openai.com/v1"

    def _generation_url(self, *, stream: bool) -> str:
        del stream
        return f"{self.base_url}/responses"

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": ([{"role": "system", "content": request.system_prompt}] if request.system_prompt else [])
            + _openai_canonical_messages(request.prompt, request.metadata),
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
            "stream": stream,
        }
        spec = _structured_output_metadata(request.metadata)
        native_tools = _native_tool_metadata(request.metadata)
        if native_tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "parameters": _thaw_json_value(item["function"]["parameters"]),
                    "strict": bool(item["function"].get("strict", True)),
                }
                for item in native_tools
            ]
            payload["tool_choice"] = "auto"
            return payload
        if spec is not None and isinstance(spec.get("name"), str) and isinstance(spec.get("json_schema"), Mapping):
            payload["text"] = {"format": {
                "type": "json_schema",
                "name": spec["name"],
                "schema": _thaw_json_value(spec["json_schema"]),
                "strict": bool(spec.get("strict", True)),
            }}
        return payload


class OpenRouterService(_OpenAICompatibleService):
    """OpenRouter OpenAI-compatible provider implementation."""

    provider = LLMProvider.OPENROUTER
    default_base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        site_url: str | None = None,
        app_name: str | None = None,
    ) -> None:
        try:
            if site_url is not None:
                _validate_text(site_url, field_name="site_url")
            if app_name is not None:
                _validate_text(app_name, field_name="app_name")
        except ValueError as exc:
            raise LLMConfigurationError(
                str(exc),
                provider=self.provider,
            ) from exc
        self.site_url = site_url
        self.app_name = app_name
        super().__init__(
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            base_url=base_url,
            client=client,
        )

    def _headers(self) -> Mapping[str, str]:
        headers = dict(super()._headers())
        if self.site_url is not None:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name is not None:
            headers["X-OpenRouter-Title"] = self.app_name
        return headers


class AnthropicService(_HTTPService):
    """Anthropic Messages API provider implementation."""

    provider = LLMProvider.ANTHROPIC
    default_base_url = "https://api.anthropic.com/v1"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        api_version: str = "2023-06-01",
    ) -> None:
        try:
            _validate_text(api_version, field_name="api_version")
        except ValueError as exc:
            raise LLMConfigurationError(
                str(exc),
                provider=self.provider,
            ) from exc
        self.api_version = api_version
        super().__init__(
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            base_url=base_url,
            client=client,
        )

    def _headers(self) -> Mapping[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }

    def _generation_url(self, *, stream: bool) -> str:
        del stream
        return f"{self.base_url}/messages"

    def _health_url(self) -> str:
        return f"{self.base_url}/models"

    def _health_params(self) -> Mapping[str, str]:
        return {"limit": "1"}

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _anthropic_canonical_messages(request.prompt, request.metadata),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if request.system_prompt is not None:
            payload["system"] = request.system_prompt
        native_tools = _native_tool_metadata(request.metadata)
        if native_tools:
            payload["tools"] = [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "input_schema": _thaw_json_value(item["function"]["parameters"]),
                }
                for item in native_tools
            ]
        return payload

class GeminiService(_HTTPService):
    """Google Gemini generate-content provider implementation."""

    provider = LLMProvider.GEMINI
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"

    def _headers(self) -> Mapping[str, str]:
        return {"Content-Type": "application/json"}

    def _params(self, *, stream: bool) -> Mapping[str, str]:
        params = {"key": self.api_key}
        if stream:
            params["alt"] = "sse"
        return params

    def _health_params(self) -> Mapping[str, str]:
        return {"key": self.api_key}

    def _generation_url(self, *, stream: bool) -> str:
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{self.base_url}/models/{self.model}:{action}"

    def _health_url(self) -> str:
        return f"{self.base_url}/models/{self.model}"

    def _payload(self, request: LLMRequest, *, stream: bool) -> dict[str, Any]:
        del stream
        payload: dict[str, Any] = {
            "contents": _gemini_canonical_messages(request.prompt, request.metadata),
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if request.system_prompt is not None:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        _apply_gemini_structured_output(payload, request.metadata)
        return payload


def _structured_output_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = metadata.get("forgex_structured_output")
    return value if isinstance(value, Mapping) else None


def _native_tool_metadata(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = metadata.get("forgex_native_tools")
    if not isinstance(value, (list, tuple)):
        return ()
    tools: list[Mapping[str, Any]] = []
    for item in value:
        function = item.get("function") if isinstance(item, Mapping) else None
        if (
            isinstance(function, Mapping)
            and isinstance(function.get("name"), str)
            and isinstance(function.get("parameters"), Mapping)
        ):
            tools.append(item)
    return tuple(tools)


def _canonical_message_metadata(metadata: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    value = metadata.get("forgex_messages")
    if not isinstance(value, (list, tuple)):
        return ()
    messages: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("role") not in {
            "user", "assistant", "tool"
        }:
            continue
        messages.append(item)
    return tuple(messages)


def _openai_canonical_messages(
    prompt: str, metadata: Mapping[str, Any]
) -> list[dict[str, Any]]:
    canonical = _canonical_message_metadata(metadata)
    if not canonical:
        return [{"role": "user", "content": prompt}]
    output: list[dict[str, Any]] = []
    for message in canonical:
        role = str(message["role"])
        content = str(message.get("content") or "")
        if role == "assistant" and isinstance(message.get("tool_calls"), (list, tuple)):
            calls = []
            for call in message["tool_calls"]:
                if not isinstance(call, Mapping):
                    continue
                calls.append({
                    "id": str(call.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(call.get("name") or ""),
                        "arguments": json.dumps(
                            _thaw_json_value(call.get("arguments") or {}),
                            ensure_ascii=True, sort_keys=True,
                        ),
                    },
                })
            output.append({"role": "assistant", "content": content or None, "tool_calls": calls})
        elif role == "tool":
            output.append({
                "role": "tool",
                "tool_call_id": str(message.get("tool_call_id") or ""),
                "name": str(message.get("name") or ""),
                "content": content,
            })
        else:
            output.append({"role": role, "content": content})
    return output


def _anthropic_canonical_messages(
    prompt: str, metadata: Mapping[str, Any]
) -> list[dict[str, Any]]:
    canonical = _canonical_message_metadata(metadata)
    if not canonical:
        return [{"role": "user", "content": prompt}]
    output: list[dict[str, Any]] = []
    for message in canonical:
        role = str(message["role"])
        content = str(message.get("content") or "")
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for call in message.get("tool_calls") or ():
                if isinstance(call, Mapping):
                    blocks.append({
                        "type": "tool_use", "id": str(call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "input": _thaw_json_value(call.get("arguments") or {}),
                    })
            output.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or ""),
                "content": content,
            }
            if output and output[-1]["role"] == "user" and isinstance(output[-1]["content"], list):
                output[-1]["content"].append(block)
            else:
                output.append({"role": "user", "content": [block]})
        else:
            output.append({"role": "user", "content": content})
    return output


def _gemini_canonical_messages(
    prompt: str, metadata: Mapping[str, Any]
) -> list[dict[str, Any]]:
    canonical = _canonical_message_metadata(metadata)
    if not canonical:
        return [{"role": "user", "parts": [{"text": prompt}]}]
    output: list[dict[str, Any]] = []
    for message in canonical:
        role = str(message["role"])
        content = str(message.get("content") or "")
        parts: list[dict[str, Any]] = []
        if content:
            parts.append({"text": content})
        if role == "assistant":
            for call in message.get("tool_calls") or ():
                if isinstance(call, Mapping):
                    parts.append({"functionCall": {
                        "name": str(call.get("name") or ""),
                        "args": _thaw_json_value(call.get("arguments") or {}),
                    }})
            output.append({"role": "model", "parts": parts})
        elif role == "tool":
            output.append({"role": "user", "parts": [{"functionResponse": {
                "name": str(message.get("name") or ""),
                "response": {"result": content},
            }}]})
        else:
            output.append({"role": "user", "parts": parts})
    return output


def _apply_openai_structured_output(
    payload: dict[str, Any], metadata: Mapping[str, Any]
) -> None:
    native_tools = _native_tool_metadata(metadata)
    if native_tools:
        payload["tools"] = _thaw_json_value(native_tools)
        payload["tool_choice"] = "auto"
        return
    spec = _structured_output_metadata(metadata)
    if spec is None:
        return
    mode = spec.get("mode")
    name = spec.get("name")
    schema = spec.get("json_schema")
    if not isinstance(name, str) or not isinstance(schema, Mapping):
        return
    if mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": name,
                "strict": bool(spec.get("strict", True)),
                # LLMRequest freezes metadata recursively. A shallow ``dict``
                # leaves nested MappingProxyType values behind, which the HTTP
                # JSON encoder cannot serialize.
                "schema": _thaw_json_value(schema),
            },
        }
    elif mode == "native_tools":
        payload["tools"] = [{
            "type": "function",
            "function": {
                "name": name,
                "description": "Return the requested structured ForgeX result.",
                "parameters": _thaw_json_value(schema),
                "strict": bool(spec.get("strict", True)),
            },
        }]
        payload["tool_choice"] = {
            "type": "function",
            "function": {"name": name},
        }


def _apply_gemini_structured_output(
    payload: dict[str, Any], metadata: Mapping[str, Any]
) -> None:
    native_tools = _native_tool_metadata(metadata)
    if native_tools:
        payload["tools"] = [{
            "functionDeclarations": [
                {
                    "name": item["function"]["name"],
                    "description": item["function"].get("description", ""),
                    "parameters": _thaw_json_value(item["function"]["parameters"]),
                }
                for item in native_tools
            ]
        }]
        payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        return
    spec = _structured_output_metadata(metadata)
    if spec is None:
        return
    mode = spec.get("mode")
    name = spec.get("name")
    schema = spec.get("json_schema")
    if not isinstance(name, str) or not isinstance(schema, Mapping):
        return
    if mode == "json_schema":
        config = payload.setdefault("generationConfig", {})
        if isinstance(config, dict):
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = _thaw_json_value(schema)
    elif mode == "native_tools":
        payload["tools"] = [{
            "functionDeclarations": [{
                "name": name,
                "description": "Return the requested structured ForgeX result.",
                "parameters": _thaw_json_value(schema),
            }]
        }]
        payload["toolConfig"] = {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [name],
            }
        }

async def _iter_sse_json(
    response: httpx.Response,
    *,
    provider: LLMProvider,
) -> AsyncIterator[Mapping[str, Any]]:
    """Yield JSON objects from a provider SSE response."""

    async for line in response.aiter_lines():
        line = line.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            yield {"type": "forgex.stream.done"}
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                "Provider stream contained invalid JSON",
                provider=provider,
                details={"payload": payload[:1000]},
            ) from exc
        if isinstance(data, Mapping):
            yield data


class _Deadline(AbstractAsyncContextManager[None]):
    """Python 3.10-compatible equivalent of ``asyncio.timeout``."""

    def __init__(self, delay: float | None) -> None:
        self._delay = delay
        self._task: asyncio.Task[Any] | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._expired = False

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("timeout requires a running asyncio task")
        self._task = task
        if self._delay is not None:
            self._handle = asyncio.get_running_loop().call_later(
                self._delay,
                self._cancel_for_timeout,
            )
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        if self._handle is not None:
            self._handle.cancel()
        if exc_type is asyncio.CancelledError and self._expired:
            raise TimeoutError from exc
        return False

    def _cancel_for_timeout(self) -> None:
        self._expired = True
        if self._task is not None:
            self._task.cancel()

    def reset(self, delay: float | None) -> None:
        if self._handle is not None:
            self._handle.cancel()
        if self._task is not None and delay is not None:
            self._handle = asyncio.get_running_loop().call_later(delay, self._cancel_for_timeout)


def _timeout_after(delay: float | None) -> _Deadline:
    return _Deadline(delay)


def _generation_timeout(request: LLMRequest, default: float) -> float | None:
    if request.metadata.get("forgex_disable_generation_timeout") is True:
        return None
    return default


def _normalize_usage(
    usage: object,
    *,
    input_key: str,
    output_key: str,
    total_key: str | None = None,
) -> Mapping[str, int]:
    if not isinstance(usage, Mapping):
        return {}
    input_tokens = _non_negative_int(usage.get(input_key))
    output_tokens = _non_negative_int(usage.get(output_key))
    total_tokens = _non_negative_int(usage.get(total_key)) if total_key else None
    normalized: dict[str, int] = {}
    if input_tokens is not None:
        normalized["input_tokens"] = input_tokens
    if output_tokens is not None:
        normalized["output_tokens"] = output_tokens
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if total_tokens is not None:
        normalized["total_tokens"] = total_tokens
    return normalized


def _retry_after_ms(value: str | None, *, milliseconds: bool = False) -> int | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        amount = float(text)
        return max(0, int(amount if milliseconds else amount * 1000))
    except ValueError:
        if milliseconds:
            return None
    try:
        when = parsedate_to_datetime(text)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0, int((when - datetime.now(timezone.utc)).total_seconds() * 1000))
    except (TypeError, ValueError, OverflowError):
        return None


def _provider_error_message_is_retryable(message: str) -> bool:
    normalized = message.casefold()
    return any(
        fragment in normalized
        for fragment in (
            "rate limit",
            "too many requests",
            "temporarily unavailable",
            "overloaded",
            "timeout",
        )
    )


def _embedded_provider_status(value: object) -> int | None:
    """Return an HTTP status carried inside a successful SSE response.

    OpenRouter may return HTTP 200 and place an upstream status such as 502
    in ``error.code``.  Codes can be JSON numbers or numeric strings.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 100 <= value <= 599 else None
    if isinstance(value, str) and value.strip().isdigit():
        status = int(value.strip())
        return status if 100 <= status <= 599 else None
    return None


def _merge_terminal_stream_event(
    current: CanonicalStreamEvent | None,
    incoming: CanonicalStreamEvent,
) -> CanonicalStreamEvent:
    if current is None:
        return incoming
    finish_reason = current.finish_reason
    if not finish_reason or (
        finish_reason == "stop" and incoming.finish_reason not in {None, "stop"}
    ):
        finish_reason = incoming.finish_reason
    return type(current)(
        "done",
        finish_reason=finish_reason,
        usage={**current.usage, **incoming.usage},
    )


def _non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _provider_error_body(
    response: httpx.Response,
) -> tuple[str, str | None, Mapping[str, Any]]:
    details: dict[str, Any] = {}
    code: str | None = None
    message = f"Provider returned HTTP {response.status_code}"
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        if response.text:
            message = response.text[:1000]
        return message, code, details

    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            raw_message = error.get("message")
            raw_code = error.get("code") or error.get("type")
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
            if isinstance(raw_code, str) and raw_code:
                code = raw_code
                details["provider_error_code"] = raw_code[:128]
            raw_type = error.get("type")
            if isinstance(raw_type, str) and raw_type:
                details["provider_error_type"] = raw_type[:128]
        elif isinstance(error, str) and error:
            message = error
    return message, code, details


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _validate_schema(
    data: Mapping[str, Any],
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} data must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            f"{label} data is missing required fields: " f"{', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{label} data contains unknown fields: " f"{', '.join(sorted(unknown))}"
        )
    return dict(data)


def _freeze_mapping(
    value: Mapping[str, Any],
    *,
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return _freeze_mapping_value(value, path=field_name, active=set())


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value
