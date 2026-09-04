from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.api.app import _latest_agent_generation_metadata, create_app
from backend.core.config import llm_max_attempts, llm_retry_backoff_seconds, llm_timeout_seconds, load_environment
from backend.model_router import ModelRouterService


def configure_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGEX_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_PROJECTS_ROOT", str(tmp_path / "projects"))
    monkeypatch.setenv("FORGEX_MODEL_ROUTER_SETTINGS_PATH", str(tmp_path / "model-settings.json"))


def test_successful_generation_metadata_is_extracted_without_name_error() -> None:
    outcome = SimpleNamespace(
        step_results=(
            SimpleNamespace(
                step=SimpleNamespace(value="GENERATE_CODE"),
                result=SimpleNamespace(
                    metadata={
                        "validation_report": {"valid": True},
                        "repair_attempt_count": 0,
                    }
                ),
            ),
        )
    )

    assert _latest_agent_generation_metadata(outcome) == {
        "validation_report": {"valid": True},
        "repair_attempt_count": 0,
    }


def test_first_run_model_bootstrap_is_persisted_and_becomes_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FORGEX_MODEL_PROVIDER", "openrouter")
    monkeypatch.setenv("FORGEX_MODEL", "vendor/firmware-model")

    first = create_app()
    first_route = first.state.model_router_service.registry.route_for_task("code_generation")
    assert first_route.provider_id == "openrouter"
    assert first_route.model_id == "vendor/firmware-model"

    # After the one-time migration, later process-environment changes must not
    # silently override what the user has persisted in Models.
    monkeypatch.setenv("FORGEX_MODEL_PROVIDER", "openai")
    monkeypatch.setenv("FORGEX_MODEL", "gpt-should-not-override")
    second = create_app()
    second_route = second.state.model_router_service.registry.route_for_task("code_generation")
    assert second_route.provider_id == "openrouter"
    assert second_route.model_id == "vendor/firmware-model"


def test_legacy_model_bootstrap_is_supported_for_one_time_migration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("FORGEX_MODEL_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_MODEL", raising=False)
    monkeypatch.setenv("PROMPTFORGE_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("PROMPTFORGE_MODEL", "legacy/bootstrap-model")

    application = create_app()
    route = application.state.model_router_service.registry.route_for_task("code_generation")
    assert route.provider_id == "openrouter"
    assert route.model_id == "legacy/bootstrap-model"


@pytest.mark.parametrize("timeout_s", [60, 180, 300])
def test_llm_timeout_configuration_flows_through_application_services(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    timeout_s: int,
) -> None:
    configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FORGEX_LLM_TIMEOUT_SECONDS", str(timeout_s))

    application = create_app()
    service = application.state.llm_service
    generation = application.state.code_generation_service

    assert isinstance(service, ModelRouterService)
    assert service.timeout_s == timeout_s
    assert generation.timeout_s == timeout_s
    assert generation.max_attempts == 2
    assert generation.retry_backoff_s == 1


def test_llm_retry_configuration_flows_to_generation_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FORGEX_LLM_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("FORGEX_LLM_RETRY_BACKOFF_SECONDS", "2.5")

    application = create_app()
    generation = application.state.code_generation_service
    assert generation.max_attempts == 4
    assert generation.retry_backoff_s == 2.5


@pytest.mark.parametrize("value", ["0", "-1", "nan", "invalid"])
def test_llm_timeout_configuration_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("FORGEX_LLM_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError, match="FORGEX_LLM_TIMEOUT_SECONDS"):
        llm_timeout_seconds()


@pytest.mark.parametrize("value", ["0", "6", "invalid"])
def test_llm_attempt_configuration_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("FORGEX_LLM_MAX_ATTEMPTS", value)
    with pytest.raises(ValueError, match="FORGEX_LLM_MAX_ATTEMPTS"):
        llm_max_attempts()


@pytest.mark.parametrize("value", ["-1", "61", "nan", "invalid"])
def test_llm_backoff_configuration_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("FORGEX_LLM_RETRY_BACKOFF_SECONDS", value)
    with pytest.raises(ValueError, match="FORGEX_LLM_RETRY_BACKOFF_SECONDS"):
        llm_retry_backoff_seconds()


def test_load_environment_reads_dotenv_without_overriding_process_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "FORGEX_MODEL_PROVIDER=openrouter\n"
        "FORGEX_MODEL=dotenv-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("FORGEX_MODEL_PROVIDER", raising=False)
    monkeypatch.setenv("FORGEX_MODEL", "process-model")

    load_environment(env_file)

    import os
    assert os.getenv("FORGEX_MODEL_PROVIDER") == "openrouter"
    assert os.getenv("FORGEX_MODEL") == "process-model"


def test_create_app_starts_without_requiring_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configure_paths(monkeypatch, tmp_path)
    for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    application = create_app()
    with TestClient(application):
        assert isinstance(application.state.llm_service, ModelRouterService)
        assert application.state.code_generation_service is not None
        assert application.state.configuration_error is None
