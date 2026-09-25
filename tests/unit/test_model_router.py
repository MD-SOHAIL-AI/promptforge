from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.model_router import (
    MemoryCredentialStore,
    ModelCapabilities,
    ModelInfo,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelRouterService,
    ProviderRegistry,
    ProviderSettingsStorage,
    UsageTracker,
)
from backend.model_router.models import ProviderHealth
from backend.model_router.providers import NvidiaNimProvider
from backend.model_router.storage import ModelRouterStorageError
from backend.model_router.registry import NVIDIA_NIM_MODELS
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
    assert registry.provider_ids() == ("openrouter", "openai", "groq", "cerebras", "nvidia_nim", "gemini", "lmstudio", "ollama", "deepseek", "anthropic")


def test_nvidia_nim_has_isolated_credentials_and_model_catalog(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    registry = ProviderRegistry(
        ProviderSettingsStorage(tmp_path / "settings.json", credential_store=credentials)
    )
    registry.configure_provider(
        "nvidia_nim", {"api_key": "nvapi-test-secret", "enabled": True}
    )

    provider = registry.provider("nvidia_nim")
    models = registry.list_models("nvidia_nim")

    assert provider.display_name == "NVIDIA NIM"
    assert provider.base_url == "https://integrate.api.nvidia.com/v1"
    assert provider.configured is True
    assert registry.api_key("nvidia_nim") == "nvapi-test-secret"
    assert registry.api_key("openai") is None
    assert models
    assert all(model.provider_id == "nvidia_nim" for model in models)
    assert len(models) == 80
    assert tuple(model.model_id for model in models) == NVIDIA_NIM_MODELS
    assert all(model.free is True for model in models)
    assert provider.default_model == "nvidia/nemotron-3-super-120b-a12b"

    adapter = NvidiaNimProvider(registry)
    service = adapter._service()
    assert service.timeout_s == 120
    assert adapter._request_metadata(ModelRequest(
        prompt="ping", metadata={"forgex_disable_generation_timeout": True}
    )) == {}
    agent_metadata = adapter._request_metadata(ModelRequest(
        prompt="build", task_type="planning"
    ))
    assert agent_metadata["forgex_nvidia_agent"] is True
    capabilities = registry.model_capabilities(
        "nvidia_nim", "nvidia/nemotron-3-super-120b-a12b"
    )
    assert capabilities.native_tools is True
    assert capabilities.reasoning is True
    assert capabilities.context_window == 1_000_000
    asyncio.run(service.aclose())


def test_nvidia_nim_expands_an_old_partial_cache_with_the_current_fallback_catalog(tmp_path: Path) -> None:
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    storage.save_provider_models(
        "nvidia_nim",
        [{
            "provider_id": "nvidia_nim",
            "model_id": "nvidia/nemotron-3-super-120b-a12b",
            "display_name": "Nemotron 3 Super",
            "free": False,
            "local": False,
        }],
    )

    models = ProviderRegistry(storage).list_models("nvidia_nim")

    assert len(models) == 80
    assert models[0].display_name == "Nemotron 3 Super"
    assert {model.model_id for model in models} == set(NVIDIA_NIM_MODELS)
    assert all(model.free is True for model in models)


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


def test_retired_forgex_openrouter_default_is_migrated(tmp_path: Path) -> None:
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    registry.configure_provider(
        "openrouter",
        {
            "api_key": "test-key",
            "enabled": True,
            "default_model": "minimax/minimax-m3:free",
        },
    )
    registry.save_route(
        ModelRoute(
            "code_generation",
            "openrouter",
            "minimax/minimax-m3:free",
        )
    )

    assert registry.default_model("openrouter") == "cohere/north-mini-code:free"
    assert registry.route_for_task("code_generation").model_id == "cohere/north-mini-code:free"


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


def test_configured_unknown_health_does_not_trigger_cross_provider_fallback(tmp_path: Path) -> None:
    primary = FakeProvider("openrouter", fail=True)
    fallback = FakeProvider("openai", content="fallback")
    router, registry = make_router(tmp_path, {"openrouter": primary, "openai": fallback})
    registry.configure_provider("openrouter", {"api_key": "one", "enabled": True})
    registry.configure_provider("openai", {"api_key": "two", "enabled": True})
    registry.save_route(ModelRoute("code_generation", "openrouter", "primary", fallback_enabled=True, fallback_provider_id="openai"))

    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation", allow_fallback=True)))

    assert len(primary.requests) == 1
    assert fallback.requests == []
    route = registry.route_for_task("code_generation")
    assert route.fallback_enabled is False
    assert route.fallback_provider_id is None


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


def test_custom_protocol_provider_is_persisted_and_installed(tmp_path: Path) -> None:
    storage = ProviderSettingsStorage(
        tmp_path / "settings.json", credential_store=MemoryCredentialStore()
    )
    registry = ProviderRegistry(storage)
    provider = registry.register_custom_provider(
        provider_id="lab_gateway",
        display_name="Lab Gateway",
        protocol="openai-responses",
        base_url="https://models.example/v1",
        default_model="reasoner",
        models=("reasoner", "vision"),
        api_key="secret-key",
    )

    assert provider.provider_id == "lab_gateway"
    assert registry.definition("lab_gateway").protocol == "openai-responses"
    assert [model.model_id for model in registry.list_models("lab_gateway")] == [
        "reasoner",
        "vision",
    ]
    assert "secret-key" not in (tmp_path / "settings.json").read_text(encoding="utf-8")
    router = ModelRouterService(registry=registry)
    assert "lab_gateway" in router._providers


def test_openrouter_capabilities_are_resolved_per_model_not_per_provider(tmp_path: Path) -> None:
    registry = ProviderRegistry(
        ProviderSettingsStorage(
            tmp_path / "settings.json", credential_store=MemoryCredentialStore()
        )
    )
    rich = ModelCapabilities(
        streaming=True,
        json_object=True,
        json_schema=True,
        native_tools=True,
        reasoning=True,
        system_prompt=True,
    )
    conservative = ModelCapabilities(streaming=True, system_prompt=True)
    registry.save_models(
        "openrouter",
        [
            ModelInfo("openrouter", "vendor/rich", "Rich", capabilities=rich),
            ModelInfo(
                "openrouter",
                "vendor/basic",
                "Basic",
                capabilities=conservative,
            ),
        ],
    )

    assert registry.model_capabilities("openrouter", "vendor/rich").json_schema is True
    assert registry.model_capabilities("openrouter", "vendor/rich").native_tools is True
    assert registry.model_capabilities("openrouter", "vendor/basic").json_schema is False
    assert registry.model_capabilities("openrouter", "vendor/unknown").native_tools is False
