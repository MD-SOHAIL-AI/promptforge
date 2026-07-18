from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import _llm_from_environment, create_app
from backend.connection_registry import ConnectionRegistry
from backend.model_router import MemoryCredentialStore, ProviderRegistry, ProviderSettingsStorage
from backend.core.config import ENV_FILE_ENV_VAR, load_environment
from backend.model_router import ModelRouterService
from backend.services.llm_service import OpenRouterService


def _verified_registry(tmp_path: Path, provider_id: str = "openrouter") -> ConnectionRegistry:
    registry = ConnectionRegistry(
        ProviderRegistry(
            ProviderSettingsStorage(
                tmp_path / "provider-settings.json",
                credential_store=MemoryCredentialStore(),
            )
        )
    )
    registry.connect(provider_id, api_key="canonical-credential", enabled=True)
    registry.record_transport_success(provider_id)
    return registry

def test_openrouter_is_registered_with_promptforge_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")
    monkeypatch.setenv("OPENROUTER_MODEL", "legacy/model-must-not-be-used")

    service = _llm_from_environment(connection_registry=_verified_registry(tmp_path))

    assert isinstance(service, OpenRouterService)
    assert service.model == "vendor/firmware-model"
    assert service.api_key == "canonical-credential"
    assert service.base_url == "https://openrouter.ai/api/v1"
    assert service.timeout_s == 180
    asyncio.run(service.aclose())


@pytest.mark.parametrize("timeout_s", [60, 180, 300])
def test_llm_timeout_configuration_flows_through_application_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout_s: int,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")
    monkeypatch.setenv("PROMPTFORGE_LLM_TIMEOUT_SECONDS", str(timeout_s))
    monkeypatch.setenv("PROMPTFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))

    application = create_app()
    service = application.state.llm_service
    generation = application.state.code_generation_service

    assert isinstance(service, ModelRouterService)
    assert service.timeout_s == timeout_s
    assert service.model == "vendor/firmware-model"
    assert generation.timeout_s == timeout_s
    assert generation.max_attempts == 2
    assert generation.retry_backoff_s == 1


def test_llm_retry_configuration_flows_to_generation_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")
    monkeypatch.setenv("PROMPTFORGE_LLM_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("PROMPTFORGE_LLM_RETRY_BACKOFF_SECONDS", "2.5")
    monkeypatch.setenv("PROMPTFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))

    application = create_app()
    generation = application.state.code_generation_service

    assert generation.max_attempts == 4
    assert generation.retry_backoff_s == 2.5


@pytest.mark.parametrize("value", ["0", "-1", "nan", "invalid"])
def test_llm_timeout_configuration_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: str,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")
    monkeypatch.setenv("PROMPTFORGE_LLM_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="PROMPTFORGE_LLM_TIMEOUT_SECONDS"):
        _llm_from_environment(connection_registry=_verified_registry(tmp_path))


def test_load_environment_reads_dotenv_without_overriding_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PROMPTFORGE_LLM_PROVIDER=OPENROUTER\n"
        "OPENROUTER_API_KEY=dotenv-key\n"
        "PROMPTFORGE_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PROMPTFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("PROMPTFORGE_MODEL", "process-model")

    load_environment(env_file)

    service = _llm_from_environment(connection_registry=_verified_registry(tmp_path))
    assert service.provider.value == "OPENROUTER"
    assert service.model == "process-model"
    asyncio.run(service.aclose())


def test_load_environment_honors_configured_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "configured.env"
    env_file.write_text("FORGEX_ENABLE_PATCH_APPLY=1\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_ENV_VAR, str(env_file))
    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)

    loaded = load_environment()

    assert loaded == env_file
    assert os.environ["FORGEX_ENABLE_PATCH_APPLY"] == "1"


def test_create_app_resolves_openrouter_configuration_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")
    monkeypatch.setenv("PROMPTFORGE_PROJECTS_ROOT", str(tmp_path / "projects"))

    application = create_app()

    with TestClient(application):
        assert isinstance(application.state.llm_service, ModelRouterService)
        assert application.state.code_generation_service is not None
        assert application.state.configuration_error is None


@pytest.mark.parametrize(
    ("missing_name", "message"),
    [
        ("PROMPTFORGE_LLM_PROVIDER", "PROMPTFORGE_LLM_PROVIDER is required"),
        ("PROMPTFORGE_MODEL", "PROMPTFORGE_MODEL is required"),
    ],
)
def test_openrouter_configuration_requires_declared_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
    message: str,
) -> None:
    values = {
        "PROMPTFORGE_LLM_PROVIDER": "OPENROUTER",
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "PROMPTFORGE_MODEL": "vendor/firmware-model",
    }
    for name, value in values.items():
        if name == missing_name:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        _llm_from_environment(connection_registry=_verified_registry(tmp_path))

def test_environment_api_key_cannot_bypass_connection_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "environment-only-secret")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")

    missing = ConnectionRegistry(
        ProviderRegistry(
            ProviderSettingsStorage(
                tmp_path / "missing-provider-settings.json",
                credential_store=MemoryCredentialStore(),
            )
        )
    )
    with pytest.raises(ValueError, match="PROVIDER_CONNECTION_NOT_READY"):
        _llm_from_environment(connection_registry=missing)


def test_legacy_llm_helper_requires_canonical_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "vendor/firmware-model")

    with pytest.raises(ValueError, match="CONNECTION_REGISTRY_REQUIRED"):
        _llm_from_environment()
