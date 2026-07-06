"""OpenAI API provider adapter."""

from __future__ import annotations

import time

import httpx

from ...services.llm_service import LLMRequest, OpenAIService
from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import BaseModelProvider, elapsed_ms


class OpenAIProvider(BaseModelProvider):
    provider_id = "openai"
    service_type = OpenAIService

    async def health(self) -> ProviderHealth:
        missing = self._configured_health()
        if missing is not None:
            return missing
        started = time.monotonic()
        try:
            service = self._service()
            try:
                ok = await service.health_check()
            finally:
                await service.aclose()
        except Exception as exc:
            return ProviderHealth(
                self.provider_id,
                False,
                "error",
                elapsed_ms(started),
                type(exc).__name__,
                str(exc),
            )
        return ProviderHealth(
            self.provider_id,
            ok,
            "connected" if ok else "error",
            elapsed_ms(started),
            None if ok else "HEALTH_CHECK_FAILED",
        )

    async def list_models(self) -> list[ModelInfo]:
        api_key = self.registry.api_key(self.provider_id)
        base_url = self.registry.base_url(self.provider_id)
        if not api_key or not base_url:
            return self.registry.list_models(self.provider_id)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
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
                    ModelInfo(
                        provider_id=self.provider_id,
                        model_id=model_id,
                        display_name=model_id.split("/")[-1],
                        free=":free" in model_id,
                    )
                )
        return models or self.registry.list_models(self.provider_id)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        service = self._service(model_id=request.model_id)
        try:
            response = await service.generate(
                LLMRequest(
                    prompt=request.prompt,
                    system_prompt=request.system_prompt,
                    temperature=request.temperature,
                    max_tokens=request.max_tokens,
                    metadata=request.metadata,
                )
            )
        finally:
            await service.aclose()
        return ModelResponse(
            content=response.content,
            provider_id=self.provider_id,
            model_id=response.model,
            latency_ms=response.latency_ms,
            input_tokens=response.token_usage.get("input_tokens"),
            output_tokens=response.token_usage.get("output_tokens"),
            total_tokens=response.token_usage.get("total_tokens"),
            raw_usage=dict(response.token_usage),
        )

    def _service(self, model_id: str | None = None) -> OpenAIService:
        api_key = self.registry.api_key(self.provider_id)
        if not api_key:
            provider = self.registry.provider(self.provider_id)
            raise ValueError(
                f"{provider.display_name} is not configured. Add your API key in Models."
            )
        return self.service_type(
            api_key=api_key,
            model=model_id or self.registry.default_model(self.provider_id),
            base_url=self.registry.base_url(self.provider_id),
        )
