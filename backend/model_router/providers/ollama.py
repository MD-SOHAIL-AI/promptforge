"""Ollama local provider adapter."""

from __future__ import annotations

import time
import json
from typing import Any, Mapping

import httpx

from ...services.llm_service import (
    LLMAuthenticationError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    _openai_canonical_messages,
)
from ..models import ModelCapabilities, ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from ..normalization import ProviderNormalizationError, normalize_response, normalize_stream_event
from .base import BaseModelProvider, elapsed_ms


class OllamaProvider(BaseModelProvider):
    provider_id = "ollama"

    async def generate_stream(self, request: ModelRequest):
        async for event in self.generate_event_stream(request):
            if event.type == "text_delta" and event.text:
                yield event.text

    async def generate_event_stream(self, request: ModelRequest):
        payload = {
            "model": request.model_id or self.registry.default_model(self.provider_id),
            "messages": _messages(request),
            "stream": True,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        model_id = str(payload["model"])
        _apply_structured_output(
            payload,
            request,
            self.registry.model_capabilities(self.provider_id, model_id),
        )
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                async with client.stream(
                    "POST",
                    f"{self.registry.base_url(self.provider_id)}/api/chat",
                    json=payload,
                ) as response:
                    _raise_ollama_status(response)
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise LLMResponseError(
                                "Ollama returned an invalid JSON stream packet.",
                                provider=LLMProvider.OLLAMA,
                                code="PROVIDER_STREAM_INVALID",
                            ) from exc
                        if not isinstance(data, dict):
                            raise LLMResponseError(
                                "Ollama returned a non-object stream packet.",
                                provider=LLMProvider.OLLAMA,
                                code="PROVIDER_STREAM_INVALID",
                            )
                        if data.get("error"):
                            raise LLMProviderError(
                                "Ollama reported a streaming error.",
                                provider=LLMProvider.OLLAMA,
                                code="PROVIDER_HTTP_ERROR",
                            )
                        try:
                            events = normalize_stream_event(data, provider_id=self.provider_id)
                        except ProviderNormalizationError as exc:
                            raise LLMResponseError(str(exc), provider=LLMProvider.OLLAMA, code=exc.code) from exc
                        for event in events:
                            yield event
                            if event.type == "done":
                                return
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                "Ollama streaming request timed out.",
                provider=LLMProvider.OLLAMA,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                "Ollama is unavailable.",
                provider=LLMProvider.OLLAMA,
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            ) from exc

    async def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.registry.base_url(self.provider_id)}/api/tags"
                )
                response.raise_for_status()
        except Exception as exc:
            return ProviderHealth(
                self.provider_id,
                False,
                "offline",
                elapsed_ms(started),
                "LOCAL_PROVIDER_OFFLINE",
                "Ollama not running. Start Ollama at http://localhost:11434.",
            )
        return ProviderHealth(self.provider_id, True, "connected", elapsed_ms(started))

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.registry.base_url(self.provider_id)}/api/tags"
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return self.registry.list_models(self.provider_id)
        raw_models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            return self.registry.list_models(self.provider_id)
        models: list[ModelInfo] = []
        for item in raw_models:
            model_id = item.get("name") if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                models.append(
                    ModelInfo(self.provider_id, model_id, model_id, local=True)
                )
        return models or self.registry.list_models(self.provider_id)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        model_id = request.model_id or self.registry.default_model(self.provider_id)
        payload: dict[str, Any] = {
            "model": model_id,
            "messages": _messages(request),
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        model_id = str(payload["model"])
        _apply_structured_output(
            payload,
            request,
            self.registry.model_capabilities(self.provider_id, model_id),
        )
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                response = await client.post(
                    f"{self.registry.base_url(self.provider_id)}/api/chat",
                    json=payload,
                )
                _raise_ollama_status(response)
                try:
                    data = response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise LLMResponseError(
                        "Ollama returned invalid JSON.",
                        provider=LLMProvider.OLLAMA,
                        code="PROVIDER_ENVELOPE_INVALID",
                    ) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                "Ollama request timed out.",
                provider=LLMProvider.OLLAMA,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                "Ollama is unavailable.",
                provider=LLMProvider.OLLAMA,
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            ) from exc
        try:
            normalized = normalize_response(data, provider_id=self.provider_id, model_id=model_id)
        except ProviderNormalizationError as exc:
            raise LLMResponseError(str(exc), provider=LLMProvider.OLLAMA, code=exc.code) from exc
        return normalized.to_response(
            provider_id=self.provider_id,
            model_id=model_id,
            latency_ms=elapsed_ms(started),
        )


def _messages(request: ModelRequest) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.extend(_openai_canonical_messages(request.prompt, request.metadata))
    return messages


def _apply_structured_output(
    payload: dict[str, Any], request: ModelRequest, capabilities: ModelCapabilities
) -> None:
    native = request.metadata.get("forgex_native_tools")
    if isinstance(native, (list, tuple)) and native:
        payload["tools"] = [dict(item) for item in native if isinstance(item, Mapping)]
        return
    spec = request.structured_output
    if spec is None:
        return
    if capabilities.json_schema:
        payload["format"] = dict(spec.json_schema)
    elif capabilities.native_tools:
        payload["tools"] = [{
            "type": "function",
            "function": {
                "name": spec.name,
                "description": "Return the requested structured ForgeX result.",
                "parameters": dict(spec.json_schema),
            },
        }]


def _raise_ollama_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    error_type: type[LLMProviderError] | type[LLMAuthenticationError] | type[LLMRateLimitError]
    if response.status_code in {401, 403}:
        error_type = LLMAuthenticationError
        code = "PROVIDER_AUTH_ERROR"
    elif response.status_code == 429:
        error_type = LLMRateLimitError
        code = "PROVIDER_RATE_LIMITED"
    else:
        error_type = LLMProviderError
        code = "PROVIDER_UNAVAILABLE" if response.status_code >= 500 else "PROVIDER_HTTP_ERROR"
    raise error_type(
        f"Ollama returned HTTP {response.status_code}.",
        provider=LLMProvider.OLLAMA,
        code=code,
        status_code=response.status_code,
        retryable=response.status_code == 429 or response.status_code >= 500,
    )


def _int_or_none(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )
