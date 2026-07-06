"""Base provider interface for model-router adapters."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from ..registry import ProviderRegistry


class BaseModelProvider(ABC):
    provider_id: str

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    @abstractmethod
    async def health(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError

    def _configured_health(self) -> ProviderHealth | None:
        provider = self.registry.provider(self.provider_id)
        if provider.configured and provider.enabled:
            return None
        return ProviderHealth(
            provider_id=self.provider_id,
            ok=False,
            status="not_configured",
            error_code="NOT_CONFIGURED",
            message=(
                f"{provider.display_name} is not configured. "
                "Add your API key in Models."
            ),
        )


def elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
