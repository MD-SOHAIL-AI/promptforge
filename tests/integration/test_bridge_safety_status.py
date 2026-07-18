from __future__ import annotations

import pytest

from backend.bridges.codex_status import CodexAlignedStatus
from tests.api.test_rollback_restore_routes import client


class _StaticCodexLogin:
    def status(self) -> CodexAlignedStatus:
        return CodexAlignedStatus(
            codex_installed=True,
            auth_status="signed_in",
            oauth_bridge_ready=True,
        )


class _CanonicalSignedOut:
    def status(self) -> CodexAlignedStatus:
        return CodexAlignedStatus(
            codex_installed=True,
            auth_status="signed_out",
            oauth_bridge_ready=False,
        )


@pytest.fixture(autouse=True)
def isolated_codex_provider_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    # An explicit zero prevents create_app() from reloading a developer .env value.
    monkeypatch.setenv("FORGEX_ENABLE_CODEX_PROVIDER", "0")


def test_bridge_safety_status_disabled_by_default(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_AGY_BRIDGE", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    monkeypatch.delenv("FORGEX_QA_MODE", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_GENERIC_BRIDGE_API", raising=False)

    with client(tmp_path) as api:
        response = api.get("/models/bridges/safety-status")

    assert response.status_code == 200
    data = response.json()
    assert data["patch_apply_enabled"] is False
    assert data["patch_apply_feature_flag"] is False
    assert data["rollback_restore_enabled"] is False
    assert data["rollback_restore_feature_flag"] is False
    assert data["agy_bridge_enabled"] is False
    assert data["qa_mode_enabled"] is False
    assert data["apply_requires_restore"] is True
    assert data["generic_bridge_contracts_available"] is True
    assert data["generic_bridge_coordinator_available"] is True
    assert data["generic_bridge_routing_enabled"] is False
    assert data["agy_generic_provider_enabled"] is False
    assert data["agy_generic_adapter_registered"] is True
    assert data["agy_generic_adapter_enabled"] is False
    assert data["agy_compatibility_router_available"] is True
    assert data["agy_effective_execution_mode"] == "blocked"
    assert data["bridge_routing_enabled"] is False
    assert data["codex_execution_enabled"] is False
    assert data["codex_provider_state"] == "product_disabled"
    assert data["codex_routing_allowed"] == data["codex_product_route_allowed"] is False
    assert data["codex_execution_enabled"] == data["codex_sandbox_execution_enabled"] is False
    assert data["codex_oauth_bridge_status"] == "qa_only"
    assert data["claude_execution_enabled"] is False
    assert data["opencode_execution_enabled"] is False
    assert data["generic_bridge_sandbox_required"] is True
    assert data["generic_bridge_event_transport_internal_only"] is False
    assert data["public_generic_run_api_enabled"] is False
    assert data["raw_instructions_persisted"] is False
    assert data["auto_apply_enabled"] is False
    assert data["auto_build_after_apply"] is False
    assert data["auto_flash_after_apply"] is False


def test_legacy_codex_status_cannot_override_canonical_authority(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_CODEX_PROVIDER", "1")

    with client(tmp_path) as api:
        api.app.state.codex_login_service = _StaticCodexLogin()
        api.app.state.connection_registry.codex_status_service = _CanonicalSignedOut()
        data = api.get("/models/bridges/safety-status").json()

    assert data["codex_cli_installed"] is True
    assert data["codex_auth_status"] == "signed_out"
    assert data["codex_model_router_available"] is False
    assert data["codex_model_router_enabled"] is True
    assert data["codex_sandbox_execution_enabled"] is True
    assert data["codex_product_route_allowed"] is False
    assert data["codex_active_workspace_mutation_allowed"] is False
    assert data["codex_routing_allowed"] is False


def test_bridge_safety_status_apply_requires_restore_flag(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)

    with client(tmp_path) as api:
        data = api.get("/models/bridges/safety-status").json()

    assert data["patch_apply_feature_flag"] is True
    assert data["rollback_restore_feature_flag"] is False
    assert data["patch_apply_enabled"] is False
    assert data["apply_enabled"] is False


def test_bridge_safety_status_qa_flags_enabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_AGY_BRIDGE", "1")
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    monkeypatch.setenv("FORGEX_QA_MODE", "1")

    with client(tmp_path) as api:
        data = api.get("/models/bridges/safety-status").json()

    assert data["agy_bridge_enabled"] is True
    assert data["qa_mode_enabled"] is True
    assert data["patch_apply_enabled"] is True
    assert data["rollback_restore_enabled"] is True
    assert data["bridge_routing_enabled"] is False
    assert data["generic_bridge_routing_enabled"] is False
    assert data["agy_generic_provider_enabled"] is False
    assert data["agy_generic_adapter_registered"] is True
    assert data["agy_generic_adapter_enabled"] is False
    assert data["agy_compatibility_router_available"] is True
    assert data["agy_effective_execution_mode"] == "legacy"
    assert data["codex_execution_enabled"] is False
    assert data["claude_execution_enabled"] is False
    assert data["opencode_execution_enabled"] is False
    assert data["auto_build_after_apply"] is False
    assert data["auto_flash_after_apply"] is False
