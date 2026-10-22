from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.model_router import (
    MemoryCredentialStore, ModelRequest, ModelResponse, ModelRoute,
    ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker,
)
from backend.model_router.catalog import ModelCatalog
from backend.model_router.models import ProviderHealth
from backend.model_router.retry import RetryPolicy
from backend.model_router.runtime import ModelRuntime
from backend.model_router.resolver import ModelResolver
from backend.services.llm_service import LLMProvider, LLMRateLimitError, LLMTimeoutError, LLMProviderError


class Provider:
    def __init__(self, provider_id: str, failures: list[Exception] | None = None):
        self.provider_id = provider_id
        self.failures = list(failures or [])
        self.requests: list[ModelRequest] = []
    async def health(self): return ProviderHealth(self.provider_id, True, "healthy")
    async def list_models(self): return []
    async def generate(self, request: ModelRequest):
        self.requests.append(request)
        if self.failures: raise self.failures.pop(0)
        return ModelResponse("ok", self.provider_id, request.model_id or "model")


def build(tmp_path: Path, providers: dict[str, Provider]):
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    for provider_id in providers:
        if provider_id in registry.provider_ids() and not registry.definition(provider_id).local:
            registry.configure_provider(provider_id, {"api_key": "key", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openrouter", "vendor/model"))
    router = ModelRouterService(registry=registry, usage=UsageTracker(tmp_path / "usage.jsonl"), providers=providers)
    return router, registry


def test_no_cross_provider_fallback_even_when_legacy_flag_is_true(tmp_path: Path):
    primary = Provider("openrouter", [RuntimeError("down")])
    alternate = Provider("openai")
    router, _ = build(tmp_path, {"openrouter": primary, "openai": alternate})
    with pytest.raises(RuntimeError):
        asyncio.run(router.generate_model(ModelRequest(prompt="x", task_type="code_generation", allow_fallback=True)))
    assert len(primary.requests) == 1
    assert alternate.requests == []


def test_same_model_timeout_retry(tmp_path: Path):
    timeout = LLMTimeoutError("timeout", provider=LLMProvider.OPENROUTER, retryable=True)
    primary = Provider("openrouter", [timeout])
    router, _ = build(tmp_path, {"openrouter": primary})
    response = asyncio.run(router.generate_model(ModelRequest(prompt="x", task_type="code_generation")))
    assert response.content == "ok"
    assert [r.model_id for r in primary.requests] == ["vendor/model", "vendor/model"]


def test_rate_limit_without_reset_retries_with_opencode_backoff(tmp_path: Path):
    error = LLMRateLimitError("limited", provider=LLMProvider.OPENROUTER, retryable=True)
    primary = Provider("openrouter", [error])
    router, _ = build(tmp_path, {"openrouter": primary})
    response = asyncio.run(router.generate_model(ModelRequest(prompt="x", task_type="code_generation")))
    assert response.content == "ok"
    assert len(primary.requests) == 2


def test_short_retry_after_retries_same_model(tmp_path: Path):
    error = LLMRateLimitError("limited", provider=LLMProvider.OPENROUTER, retryable=True, details={"retry_after_ms": 1})
    primary = Provider("openrouter", [error])
    router, _ = build(tmp_path, {"openrouter": primary})
    response = asyncio.run(router.generate_model(ModelRequest(prompt="x", task_type="code_generation")))
    assert response.content == "ok"
    assert len(primary.requests) == 2


def test_long_retry_after_does_not_retry(tmp_path: Path):
    error = LLMRateLimitError("limited", provider=LLMProvider.OPENROUTER, retryable=True, details={"retry_after_ms": 86_400_000})
    primary = Provider("openrouter", [error])
    router, _ = build(tmp_path, {"openrouter": primary})
    with pytest.raises(LLMRateLimitError):
        asyncio.run(router.generate_model(ModelRequest(prompt="x", task_type="code_generation")))
    assert len(primary.requests) == 1


def test_nvidia_embedding_model_rejected_for_generation(tmp_path: Path):
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    registry.configure_provider("nvidia_nim", {"api_key": "key", "enabled": True})
    resolver = ModelResolver(registry, ModelCatalog(registry))
    with pytest.raises(LLMProviderError) as caught:
        resolver.resolve(ModelRequest(prompt="x", provider_id="nvidia_nim", model_id="nvidia/nv-embedqa-mistral-7b-v2"))
    assert caught.value.code == "MODEL_CAPABILITY_MISMATCH"
