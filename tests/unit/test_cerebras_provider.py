from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from backend.model_router import MemoryCredentialStore, ModelRouterService, ProviderRegistry, ProviderSettingsStorage
from backend.model_router.providers.cerebras import CerebrasProvider


def _registry(tmp_path: Path) -> ProviderRegistry:
    return ProviderRegistry(ProviderSettingsStorage(
        tmp_path / "settings.json", credential_store=MemoryCredentialStore()
    ))


def test_cerebras_is_builtin_and_routes_through_its_adapter(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry.provider("cerebras").base_url == "https://api.cerebras.ai/v1"
    assert registry.provider("cerebras").default_model == "gpt-oss-120b"
    assert isinstance(ModelRouterService(registry)._providers["cerebras"], CerebrasProvider)


def test_cerebras_imports_every_model_returned_for_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry(tmp_path)
    registry.configure_provider("cerebras", {"api_key": "test-secret", "enabled": True})
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "gpt-oss-120b", "name": "GPT OSS"},
            {"id": "zai-glm-4.7"},
            {"id": "my-private-model"},
            {"id": "gpt-oss-120b"},
        ]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    models = asyncio.run(CerebrasProvider(registry).list_models())

    assert [model.model_id for model in models] == ["gpt-oss-120b", "zai-glm-4.7", "my-private-model"]
    assert models[0].display_name == "GPT OSS"
    assert str(requests[0].url) == "https://api.cerebras.ai/v1/models"
    assert requests[0].headers["Authorization"] == "Bearer test-secret"
    registry.save_models("cerebras", models)
    assert [model.model_id for model in registry.list_models("cerebras")] == [
        "gpt-oss-120b", "zai-glm-4.7", "my-private-model"
    ]


def test_cerebras_discovery_failure_is_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry(tmp_path)
    registry.configure_provider("cerebras", {"api_key": "test-secret", "enabled": True})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(401)), **kwargs
    ))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(CerebrasProvider(registry).list_models())
