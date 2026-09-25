"""LM Studio local OpenAI-compatible provider adapter."""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import elapsed_ms
from .openai_provider import OpenAIProvider
from ...services.llm_service import LLMProvider, LLMResponseError, OpenAIService
from ..normalization import ProviderNormalizationError, normalize_response


class LMStudioProvider(OpenAIProvider):
    provider_id = "lmstudio"

    def _service(self, model_id: str | None = None):
        return OpenAIService(
            api_key="local",
            model=model_id or self.registry.default_model(self.provider_id),
            base_url=self.registry.base_url(self.provider_id),
        )

    async def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.registry.base_url(self.provider_id)}/models"
                )
                response.raise_for_status()
        except Exception as exc:
            return ProviderHealth(
                self.provider_id,
                False,
                "offline",
                elapsed_ms(started),
                "LOCAL_PROVIDER_OFFLINE",
                "LM Studio server not running. Start the local server at http://localhost:1234/v1.",
            )
        return ProviderHealth(self.provider_id, True, "connected", elapsed_ms(started))

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.registry.base_url(self.provider_id)}/models"
                )
                response.raise_for_status()
                data = response.json()
        except Exception:
            return self.registry.list_models(self.provider_id)
        raw_models = data.get("data") if isinstance(data, dict) else None
        if not isinstance(raw_models, list):
            return self.registry.list_models(self.provider_id)
        models: list[ModelInfo] = []
        for item in raw_models:
            model_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(model_id, str) and model_id.strip():
                models.append(
                    ModelInfo(self.provider_id, model_id, model_id, local=True)
                )
        return models or self.registry.list_models(self.provider_id)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await super().generate(request)


def _messages(request: ModelRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.prompt})
    return messages


def _int_or_none(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )
