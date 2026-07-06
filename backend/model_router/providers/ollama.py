"""Ollama local provider adapter."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import BaseModelProvider, elapsed_ms


class OllamaProvider(BaseModelProvider):
    provider_id = "ollama"

    async def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.registry.base_url(self.provider_id)}/api/tags")
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
                response = await client.get(f"{self.registry.base_url(self.provider_id)}/api/tags")
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
                models.append(ModelInfo(self.provider_id, model_id, model_id, local=True))
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
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{self.registry.base_url(self.provider_id)}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama returned an invalid response: empty content")
        input_tokens = _int_or_none(data.get("prompt_eval_count")) if isinstance(data, dict) else None
        output_tokens = _int_or_none(data.get("eval_count")) if isinstance(data, dict) else None
        total_tokens = input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None
        return ModelResponse(
            content=content,
            provider_id=self.provider_id,
            model_id=model_id,
            latency_ms=elapsed_ms(started),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            raw_usage={
                "prompt_eval_count": input_tokens,
                "eval_count": output_tokens,
            },
        )


def _messages(request: ModelRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.prompt})
    return messages


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
