"""Cerebras chat completions and account-specific model discovery."""

from __future__ import annotations

import httpx

from ..models import ModelInfo
from .openai_provider import OpenAIProvider


class CerebrasProvider(OpenAIProvider):
    provider_id = "cerebras"

    async def list_models(self) -> list[ModelInfo]:
        api_key = self.registry.api_key(self.provider_id)
        base_url = self.registry.base_url(self.provider_id)
        if not api_key or not base_url:
            raise ValueError("Configure a Cerebras API key to import models.")

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            payload = response.json()

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Cerebras returned an invalid model list.")

        models: list[ModelInfo] = []
        seen: set[str] = set()
        for item in items:
            model_id = item.get("id") if isinstance(item, dict) else None
            if not isinstance(model_id, str) or not model_id.strip():
                continue
            model_id = model_id.strip()
            if model_id in seen:
                continue
            seen.add(model_id)
            models.append(ModelInfo(
                provider_id=self.provider_id,
                model_id=model_id,
                display_name=str(item.get("name") or model_id),
                capabilities=self.registry.model_capabilities(self.provider_id, model_id),
            ))
        if not models:
            raise ValueError("Cerebras returned no usable models.")
        return models
