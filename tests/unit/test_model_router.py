from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.model_router import (
    MemoryCredentialStore,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelRouterService,
    ProviderRegistry,
    ProviderSettingsStorage,
    UsageTracker,
)
from backend.model_router.models import ProviderHealth
from backend.model_router.storage import ModelRouterStorageError
from backend.services.llm_service import LLMProviderError, LLMRequest


class FakeProvider:
    def __init__(self, provider_id: str, *, content: str = "ok", fail: bool = False) -> None:
        self.provider_id = provider_id
        self.content = content
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.provider_id, not self.fail, "connected" if not self.fail else "error")

    async def list_models(self) -> list[object]:
        return []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider failed")
        return ModelResponse(self.content, self.provider_id, request.model_id or "model", latency_ms=3)


def make_router(tmp_path: Path, providers: dict[str, FakeProvider]):
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    usage = UsageTracker(tmp_path / "usage.jsonl")
    return ModelRouterService(registry=registry, usage=usage, providers=providers), registry


def test_registry_contains_only_supported_model_providers(tmp_path: Path) -> None:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))
    assert registry.provider_ids() == ("openrouter", "openai", "groq", "gemini", "lmstudio", "ollama")


def test_persisted_selection_is_authoritative_over_legacy_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    openai = FakeProvider("openai")
    openrouter = FakeProvider("openrouter")
    router, registry = make_router(tmp_path, {"openai": openai, "openrouter": openrouter})
    registry.configure_provider("openai", {"api_key": "test-key", "enabled": True})
    registry.configure_provider("openrouter", {"api_key": "test-key", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openai", "gpt-4.1-mini", fallback_enabled=False))
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "should-not-override")

    response = asyncio.run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "openai"
    assert openai.requests[0].model_id == "gpt-4.1-mini"
    assert openrouter.requests == []


def test_default_model_strips_accidental_wrapping_quotes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)

    monkeypatch.setenv("OPENROUTER_MODEL", "nvidia/nemotron:free\"")

    assert registry.default_model("openrouter") == "nvidia/nemotron:free"


def test_explicit_request_override_still_wins(tmp_path: Path) -> None:
    openai = FakeProvider("openai")
    openrouter = FakeProvider("openrouter")
    router, registry = make_router(tmp_path, {"openai": openai, "openrouter": openrouter})
    registry.configure_provider("openai", {"api_key": "one", "enabled": True})
    registry.configure_provider("openrouter", {"api_key": "two", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openai", "gpt-4.1-mini", fallback_enabled=False))

    response = asyncio.run(router.generate_model(ModelRequest(
        prompt="hello", task_type="code_generation", provider_id="openrouter", model_id="custom/model", allow_fallback=False,
    )))

    assert response.provider_id == "openrouter"
    assert openrouter.requests[0].model_id == "custom/model"


def test_configured_unknown_health_fallback_is_attempted(tmp_path: Path) -> None:
    primary = FakeProvider("openrouter", fail=True)
    fallback = FakeProvider("openai", content="fallback")
    router, registry = make_router(tmp_path, {"openrouter": primary, "openai": fallback})
    registry.configure_provider("openrouter", {"api_key": "one", "enabled": True})
    registry.configure_provider("openai", {"api_key": "two", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openrouter", "primary", fallback_enabled=True, fallback_provider_id="openai"))

    response = asyncio.run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "openai"
    assert len(primary.requests) == 1
    assert len(fallback.requests) == 1


def test_router_does_not_cross_cloud_local_fallback_boundary(tmp_path: Path) -> None:
    router, registry = make_router(tmp_path, {"openrouter": FakeProvider("openrouter"), "ollama": FakeProvider("ollama")})
    registry.configure_provider("openrouter", {"api_key": "one", "enabled": True})
    registry.save_health("ollama", {"status": "unknown"})
    candidates = router._candidate_provider_ids(
        "openrouter", allow_fallback=True, local_only=False, task_type="code_generation", fallback_provider_id="ollama",
    )
    assert candidates == ("openrouter",)


def test_streaming_contract_fails_explicitly_when_adapter_has_no_stream(tmp_path: Path) -> None:
    provider = FakeProvider("openai")
    router, registry = make_router(tmp_path, {"openai": provider})
    registry.configure_provider("openai", {"api_key": "one", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openai", "gpt-4.1-mini", fallback_enabled=False))

    async def consume() -> None:
        async for _ in router.generate_stream(LLMRequest(prompt="hello", metadata={"task_type": "code_generation"})):
            pass

    with pytest.raises(LLMProviderError) as caught:
        asyncio.run(consume())
    assert caught.value.retryable is False
    assert caught.value.details["streaming_supported"] is False


def test_corrupt_model_settings_are_not_silently_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not-json", encoding="utf-8")
    storage = ProviderSettingsStorage(path, credential_store=MemoryCredentialStore())
    with pytest.raises(ModelRouterStorageError):
        storage.save_provider_health("openai", {"status": "ready"})
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_provider_settings_write_never_persists_plaintext_secret(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    storage = ProviderSettingsStorage(path, credential_store=MemoryCredentialStore())
    storage.save_provider_settings("openai", {"api_key": "super-secret", "enabled": True})
    assert "super-secret" not in path.read_text(encoding="utf-8")
    assert storage.api_key("openai") == "super-secret"
