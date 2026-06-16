"""Unified async LLM provider interface for PromptForge AI.

This module owns provider communication only. It contains no planning,
coordination, prompt construction policy, or other application business logic.
Provider integrations use documented HTTP APIs through ``httpx`` so callers
can inject a client or transport for testing and deployment-specific control.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, ClassVar

import httpx

from ..core.config import DEFAULT_LLM_TIMEOUT_SECONDS
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

    def __post_init__(self) -> None:
        _validate_text(self.content, field_name="content")
        if not isinstance(self.provider, LLMProvider):
            raise ValueError("provider must be an LLMProvider")
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
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    "token_usage values must be non-negative integers"
                )
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

    default_code = "TIMEOUT"


class LLMAuthenticationError(LLMError):
    """The provider rejected supplied authentication credentials."""

    default_code = "AUTHENTICATION_ERROR"


class LLMRateLimitError(LLMError):
    """The provider rejected a request due to quota or rate limits."""

    default_code = "RATE_LIMITED"


class LLMProviderError(LLMError):
    """The provider returned a non-success response."""

    default_code = "PROVIDER_ERROR"


class LLMResponseError(LLMError):
    """A successful provider response had an invalid or empty shape."""

    default_code = "INVALID_RESPONSE"


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
        try:
            async with _timeout_after(self.timeout_s):
                response = await self._client.post(
                    self._generation_url(stream=False),
                    headers=self._headers(),
                    params=self._params(stream=False),
                    json=self._payload(request, stream=False),
                    timeout=self.timeout_s,
                )
                self._raise_for_status(response)
                data = self._decode_json(response)
                content, usage, response_model = self._parse_response(data)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise self._timeout_error("generation") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"{self.provider.value} request failed: {exc}",
                provider=self.provider,
                retryable=True,
                details={"exception_type": type(exc).__name__},
            ) from exc

        return LLMResponse(
            content=content,
            provider=self.provider,
            model=response_model or self.model,
            token_usage=usage,
            latency_ms=_elapsed_ms(started),
        )

    async def generate_stream(
        self,
        request: LLMRequest,
    ) -> AsyncIterator[str]:
        if not isinstance(request, LLMRequest):
            raise TypeError("request must be an LLMRequest")
        try:
            async with _timeout_after(self.timeout_s):
                async with self._client.stream(
                    "POST",
                    self._generation_url(stream=True),
                    headers=self._headers(),
                    params=self._params(stream=True),
                    json=self._payload(request, stream=True),
                    timeout=self.timeout_s,
                ) as response:
                    self._raise_for_status(response)
                    async for data in _iter_sse_json(
                        response,
                        provider=self.provider,
                    ):
                        for delta in self._parse_stream_event(data):
                            if delta:
                                yield delta
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise self._timeout_error("streaming generation") from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                f"{self.provider.value} streaming request failed: {exc}",
                provider=self.provider,
                retryable=True,
                details={"exception_type": type(exc).__name__},
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

    def _timeout_error(self, operation: str) -> LLMTimeoutError:
        return LLMTimeoutError(
            f"{self.provider.value} {operation} timed out after "
            f"{self.timeout_s:g} seconds",
            provider=self.provider,
            retryable=True,
            details={"timeout_s": self.timeout_s, "operation": operation},
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        message, code, details = _provider_error_body(response)
        common = {
            "provider": self.provider,
            "status_code": response.status_code,
            "details": details,
        }
        if response.status_code in {401, 403}:
            raise LLMAuthenticationError(message, code=code, **common)
        if response.status_code == 429:
            raise LLMRateLimitError(
                message,
                code=code,
                retryable=True,
                **common,
            )
        raise LLMProviderError(
            message,
            code=code,
            retryable=response.status_code >= 500,
            **common,
        )

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

    @abstractmethod
    def _parse_response(
        self,
        data: Mapping[str, Any],
    ) -> tuple[str, Mapping[str, int], str | None]:
        raise NotImplementedError

    @abstractmethod
    def _parse_stream_event(self, data: Mapping[str, Any]) -> tuple[str, ...]:
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
        messages: list[dict[str, str]] = []
        if request.system_prompt is not None:
            messages.append(
                {"role": "system", "content": request.system_prompt}
            )
        messages.append({"role": "user", "content": request.prompt})
        return {
            "model": self.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }

    def _parse_response(
        self,
        data: Mapping[str, Any],
    ) -> tuple[str, Mapping[str, int], str | None]:
        try:
            choices = data["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise self._invalid_response("missing choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise self._invalid_response("response content is empty")
        usage = data.get("usage")
        normalized = _normalize_usage(
            usage,
            input_key="prompt_tokens",
            output_key="completion_tokens",
            total_key="total_tokens",
        )
        model = data.get("model")
        return content, normalized, model if isinstance(model, str) else None

    def _parse_stream_event(self, data: Mapping[str, Any]) -> tuple[str, ...]:
        try:
            choices = data.get("choices", ())
            delta = choices[0].get("delta", {}) if choices else {}
            content = delta.get("content")
        except (AttributeError, IndexError, TypeError):
            return ()
        return (content,) if isinstance(content, str) and content else ()

    def _invalid_response(self, reason: str) -> LLMResponseError:
        return LLMResponseError(
            f"{self.provider.value} returned an invalid response: {reason}",
            provider=self.provider,
        )


class OpenAIService(_OpenAICompatibleService):
    """OpenAI chat-completion provider implementation."""

    provider = LLMProvider.OPENAI
    default_base_url = "https://api.openai.com/v1"


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
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": stream,
        }
        if request.system_prompt is not None:
            payload["system"] = request.system_prompt
        return payload

    def _parse_response(
        self,
        data: Mapping[str, Any],
    ) -> tuple[str, Mapping[str, int], str | None]:
        blocks = data.get("content")
        if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
            raise self._invalid_response("content must be a sequence")
        parts = [
            block.get("text")
            for block in blocks
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        content = "".join(parts)
        if not content.strip():
            raise self._invalid_response("response content is empty")
        normalized = _normalize_usage(
            data.get("usage"),
            input_key="input_tokens",
            output_key="output_tokens",
        )
        model = data.get("model")
        return content, normalized, model if isinstance(model, str) else None

    def _parse_stream_event(self, data: Mapping[str, Any]) -> tuple[str, ...]:
        if data.get("type") != "content_block_delta":
            return ()
        delta = data.get("delta")
        if not isinstance(delta, Mapping) or delta.get("type") != "text_delta":
            return ()
        text = delta.get("text")
        return (text,) if isinstance(text, str) and text else ()

    def _invalid_response(self, reason: str) -> LLMResponseError:
        return LLMResponseError(
            f"ANTHROPIC returned an invalid response: {reason}",
            provider=self.provider,
        )


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
            "contents": [
                {"role": "user", "parts": [{"text": request.prompt}]}
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        if request.system_prompt is not None:
            payload["systemInstruction"] = {
                "parts": [{"text": request.system_prompt}]
            }
        return payload

    def _parse_response(
        self,
        data: Mapping[str, Any],
    ) -> tuple[str, Mapping[str, int], str | None]:
        content = self._candidate_text(data)
        if not content.strip():
            raise self._invalid_response("response content is empty")
        normalized = _normalize_usage(
            data.get("usageMetadata"),
            input_key="promptTokenCount",
            output_key="candidatesTokenCount",
            total_key="totalTokenCount",
        )
        model = data.get("modelVersion")
        return content, normalized, model if isinstance(model, str) else None

    def _parse_stream_event(self, data: Mapping[str, Any]) -> tuple[str, ...]:
        content = self._candidate_text(data)
        return (content,) if content else ()

    def _candidate_text(self, data: Mapping[str, Any]) -> str:
        try:
            candidates = data.get("candidates", ())
            parts = candidates[0]["content"]["parts"] if candidates else ()
        except (KeyError, IndexError, TypeError):
            return ""
        return "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, Mapping) and isinstance(part.get("text"), str)
        )

    def _invalid_response(self, reason: str) -> LLMResponseError:
        return LLMResponseError(
            f"GEMINI returned an invalid response: {reason}",
            provider=self.provider,
        )


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
        if not payload or payload == "[DONE]":
            continue
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

    def __init__(self, delay: float) -> None:
        self._delay = delay
        self._task: asyncio.Task[Any] | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._expired = False

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("timeout requires a running asyncio task")
        self._task = task
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


def _timeout_after(delay: float) -> _Deadline:
    return _Deadline(delay)


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
    total_tokens = (
        _non_negative_int(usage.get(total_key)) if total_key else None
    )
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
        details = dict(body)
        error = body.get("error", body)
        if isinstance(error, Mapping):
            raw_message = error.get("message")
            raw_code = error.get("code") or error.get("type")
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
            if isinstance(raw_code, str) and raw_code:
                code = raw_code
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
            f"{label} data is missing required fields: "
            f"{', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{label} data contains unknown fields: "
            f"{', '.join(sorted(unknown))}"
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
