from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.model_router import MemoryCredentialStore, ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.services.project_service import ProjectService
from backend.settings import SettingsService


def client(tmp_path: Path) -> TestClient:
    router = ModelRouterService(
        ProviderRegistry(ProviderSettingsStorage(tmp_path / "model-router.json", credential_store=MemoryCredentialStore())),
        UsageTracker(tmp_path / "usage.jsonl"),
    )
    app = create_app(
        model_router_service=router,
        project_service=ProjectService(tmp_path / "projects"),
        settings_service=SettingsService(tmp_path / "settings.json"),
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False)


def test_settings_defaults_and_schema_load(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.get("/settings")
        schema = api.get("/settings/schema")

    assert response.status_code == 200
    assert response.json()["settings"]["appearance.theme"] == "forgex-dark"
    assert schema.status_code == 200
    assert schema.json()["schema"]["appearance.theme"]["default"] == "forgex-dark"


def test_settings_patch_persists_values(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        patched = api.patch("/settings", json={"settings": {"appearance.theme": "light"}})
        listed = api.get("/settings")

    assert patched.status_code == 200
    assert listed.json()["settings"]["appearance.theme"] == "light"


def test_settings_reset_restores_defaults(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        api.patch("/settings", json={"settings": {"appearance.theme": "light"}})
        reset = api.post("/settings/reset")

    assert reset.status_code == 200
    assert reset.json()["settings"]["appearance.theme"] == "forgex-dark"


def test_settings_export_excludes_secrets(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        api.post(
            "/models/providers/openrouter/configure",
            json={"api_key": "sk-test-secret-abcd", "enabled": True},
        )
        exported = api.post("/settings/export")

    assert exported.status_code == 200
    assert exported.json()["secrets_included"] is False
    assert "sk-test-secret-abcd" not in exported.text


def test_invalid_setting_key_is_rejected(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.patch("/settings", json={"settings": {"unknown.key": True}})

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SETTING"
