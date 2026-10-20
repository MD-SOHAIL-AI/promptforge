from __future__ import annotations

from dataclasses import replace

from .models import ModelCapabilities, ModelInfo, ModelProvider, ProviderStatus
from .registry import ProviderRegistry


_NON_GENERATIVE_TOKENS = (
    "embed", "embedding", "rerank", "reward", "guard", "safety", "translate",
)


class ModelCatalog:
    """Read-only facts about configured providers and concrete models."""

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def provider(self, provider_id: str) -> ModelProvider:
        return self.registry.provider(provider_id)

    def providers(self) -> list[ModelProvider]:
        return self.registry.list_providers()

    def models(self, provider_id: str) -> list[ModelInfo]:
        with self.registry.storage.read_snapshot():
            return self._models(provider_id)

    def _models(self, provider_id: str) -> list[ModelInfo]:
        result: list[ModelInfo] = []
        for model in self.registry.list_models(provider_id):
            caps = self.capabilities(provider_id, model.model_id)
            result.append(replace(model, capabilities=caps))
        return result

    def capabilities(self, provider_id: str, model_id: str) -> ModelCapabilities:
        caps = self.registry.model_capabilities(provider_id, model_id)
        lowered = model_id.casefold()
        if provider_id == "nvidia_nim" and any(token in lowered for token in _NON_GENERATIVE_TOKENS):
            is_embed = "embed" in lowered
            is_rerank = "rerank" in lowered or "reward" in lowered
            return replace(
                caps,
                text_generation=False,
                embeddings=is_embed,
                reranking=is_rerank,
                native_tools=False,
                json_object=False,
                json_schema=False,
                reasoning=False,
            )
        return caps

    def status(self, provider_id: str) -> ProviderStatus:
        provider = self.provider(provider_id)
        if not provider.enabled:
            configuration = "missing_credentials" if not provider.configured else "disabled"
        else:
            configuration = "configured"
        raw = provider.health_status.strip().casefold()
        runtime = "unknown"
        if raw in {"ok", "healthy", "ready", "available"}:
            runtime = "healthy"
        elif raw in {"degraded", "rate_limited", "rate_limit"}:
            runtime = "degraded"
        elif raw in {"offline", "unavailable", "error"}:
            runtime = "unavailable"
        return ProviderStatus(provider_id, configuration, runtime, provider.last_error)
