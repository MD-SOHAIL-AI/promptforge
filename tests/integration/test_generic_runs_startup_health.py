from __future__ import annotations

from pathlib import Path

import pytest

from tests.api.test_antigravity_bridge_routes import make_client


def test_sse_registration_does_not_delay_backend_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    with make_client(tmp_path, enabled=False) as api:
        health = api.get("/health")
        missing = api.get("/models/bridges/runs/qa-run-missing/events")

    assert health.status_code == 200
    assert missing.status_code == 404
    assert missing.json() == {
        "code": "GENERIC_RUN_NOT_FOUND",
        "message": "Generic run was not found.",
        "details": {},
    }


@pytest.mark.parametrize(
    "fixture_state",
    [
        "disabled",
        "ready",
        "submitting",
        "queued",
        "validating",
        "preparing_sandbox",
        "running",
        "collecting_artifacts",
        "completed",
        "blocked",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
        "resync_required",
        "backend_unavailable",
    ],
)
def test_qa_visual_fixtures_are_inert_and_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_state: str
) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_QA_MODE", "1")
    with make_client(tmp_path, enabled=False) as api:
        response = api.get(f"/models/bridges/generic/qa-fixtures/{fixture_state}")

    assert response.status_code == 200
    serialized = response.text.casefold()
    assert "workspace_root" not in serialized
    assert "instruction" not in serialized
    assert "stdout" not in serialized and "stderr" not in serialized
    assert api.app.state.bridge_agy_runner.popen_factory.calls == []


def test_qa_visual_fixtures_are_absent_outside_qa_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.delenv("FORGEX_QA_MODE", raising=False)
    with make_client(tmp_path, enabled=False) as api:
        response = api.get("/models/bridges/generic/qa-fixtures/running")

    assert response.status_code == 404


def test_safety_diagnostics_report_disabled_defaults_and_available_sse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    for name in (
        "FORGEX_ENABLE_AGY_BRIDGE",
        "FORGEX_ENABLE_GENERIC_BRIDGE_API",
        "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
        "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
        "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
    ):
        monkeypatch.setenv(name, "0")
    with make_client(tmp_path, enabled=False) as api:
        safety = api.get("/models/bridges/safety-status")

    assert safety.status_code == 200
    payload = safety.json()
    assert payload["generic_api_status"] == "available_disabled"
    assert payload["generic_sse_status"] == "available"
    assert payload["generic_provider_scope"] == "agy_only"
    assert payload["agy_generic_execution_default"] == "disabled"

