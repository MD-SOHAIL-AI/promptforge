"""OpenAI API provider adapter."""

from __future__ import annotations

import time

import httpx

from ...services.llm_service import LLMRequest, OpenAIService
from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import BaseModelProvider, elapsed_ms


def _catalog_model_is_free(item: dict, model_id: str) -> bool | None:
    if ":free" in model_id:
        return True
    pricing = item.get("pricing")
    if not isinstance(pricing, dict):
        return None
    values: list[float] = []
    for key in ("prompt", "completion", "request", "image", "web_search", "internal_reasoning"):
        raw = pricing.get(key)
        if raw is None:
            continue
        try:
            values.append(float(str(raw)))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return all(value == 0 for value in values)


def _catalog_supported_parameters(item: dict) -> tuple[str, ...]:
    raw = item.get("supported_parameters")
    if not isinstance(raw, list):
        return ()
    return tuple(
        value
        for value in raw[:64]
        if isinstance(value, str)
        and 0 < len(value) <= 64
        and all(character.isalnum() or character == "_" for character in value)
    )


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
        if not base_url:
            return self.registry.list_models(self.provider_id)
        if not api_key and self.provider_id != "openrouter":
            return self.registry.list_models(self.provider_id)
        try:
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            params = {"sort": "pricing-low-to-high"} if self.provider_id == "openrouter" else None
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{base_url}/models",
                    headers=headers,
                    params=params,
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
                        display_name=item.get("name") if isinstance(item.get("name"), str) else model_id.split("/")[-1],
                        context_window=item.get("context_length") if isinstance(item.get("context_length"), int) else None,
                        free=_catalog_model_is_free(item, model_id),
                        supported_parameters=_catalog_supported_parameters(item),
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
