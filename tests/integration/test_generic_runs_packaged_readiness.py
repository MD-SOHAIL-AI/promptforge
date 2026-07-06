from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.providers.agy_generic import AGYBridgeProvider
from tests.api.test_antigravity_bridge_routes import make_client


GENERIC_FLAGS = (
    "FORGEX_ENABLE_AGY_BRIDGE",
    "FORGEX_ENABLE_GENERIC_BRIDGE_API",
    "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
    "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
    "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
)


def _disabled_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "packaged-root"
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(root))
    for name in GENERIC_FLAGS:
        monkeypatch.setenv(name, "0")
    return root


def test_packaged_app_imports_generic_routes_without_detection_or_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disabled_environment(monkeypatch, tmp_path)

    async def unexpected_detection(self):  # pragma: no cover - called only on regression
        raise AssertionError("provider detection ran during app composition")

    monkeypatch.setattr(AGYBridgeProvider, "detect", unexpected_detection)
    with make_client(tmp_path, enabled=False) as api:
        health = api.get("/health")
        listing = api.get("/models/bridges/generic/runs")
        routes = {route.path for route in api.app.routes}

    assert health.status_code == 200
    assert listing.status_code == 200 and listing.json()["runs"] == []
    assert "/models/bridges/providers" in routes
    assert "/models/bridges/runs/{run_id}/events" in routes
    assert api.app.state.bridge_agy_runner.popen_factory.calls == []


def test_packaged_provider_listing_is_agy_only_without_agy_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disabled_environment(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=False, resolver=lambda command: None) as api:
        response = api.get("/models/bridges/providers")

    assert response.status_code == 200
    assert [item["provider_id"] for item in response.json()["providers"]] == ["agy"]
    provider = response.json()["providers"][0]
    assert provider["installed"] is False
    assert provider["execution_enabled"] is False


def test_packaged_default_start_is_disabled_and_does_not_create_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disabled_environment(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=False) as api:
        response = api.post(
            "/models/bridges/runs",
            json={
                "provider_id": "agy",
                "project_id": "qa-project-safe",
                "instruction": "Sanitized QA request.",
                "timeout_seconds": 30,
                "idempotency_key": "qa-packaged-disabled-001",
            },
        )
        listing = api.get("/models/bridges/generic/runs")

    assert response.status_code == 403
    assert response.json()["code"] == "generic_execution_disabled"
    assert listing.json()["count"] == 0
    assert api.app.state.bridge_agy_runner.popen_factory.calls == []


def test_packaged_corrupt_generic_store_does_not_break_health_or_safety_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _disabled_environment(monkeypatch, tmp_path)
    store = root / ".promptforge" / "state" / "generic-bridge-runs.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{corrupt qa metadata\n", encoding="utf-8")

    with make_client(tmp_path, enabled=False) as api:
        health = api.get("/health")
        safety = api.get("/models/bridges/safety-status")
        listing = api.get("/models/bridges/generic/runs")

    assert health.status_code == 200
    assert safety.status_code == 200
    assert safety.json()["generic_run_store_status"] == "unavailable"
    assert listing.status_code == 500
    assert listing.json()["code"] == "RECORD_CORRUPT"

