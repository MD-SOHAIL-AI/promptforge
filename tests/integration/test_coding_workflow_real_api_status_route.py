from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_coding_workflow_real_api_route import StubModelRouter
from tests.integration.test_coding_workflow_routes import BASE, configure_env, make_route_rig


STATUS_ROUTE = f"{BASE}/api/status"


def configure_real_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    unified: bool,
    real: bool,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=unified, fake=False)
    monkeypatch.setenv("FORGEX_ENABLE_REAL_API_CODING_AGENT", "1" if real else "0")


def test_status_route_reports_disabled_flags_without_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=False, real=True)
    router = StubModelRouter()
    rig = make_route_rig(tmp_path)
    rig.api.app.state.model_router_service = router

    unified_disabled = rig.api.get(STATUS_ROUTE)

    assert unified_disabled.status_code == 200
    assert unified_disabled.json()["ready"] is False
    assert unified_disabled.json()["reason"] == "UNIFIED_CODING_WORKFLOW_DISABLED"
    assert router.requests == []

    configure_real_env(monkeypatch, tmp_path / "real-disabled", unified=True, real=False)
    router2 = StubModelRouter()
    rig2 = make_route_rig(tmp_path / "real-disabled")
    rig2.api.app.state.model_router_service = router2
    real_disabled = rig2.api.get(STATUS_ROUTE)

    assert real_disabled.status_code == 200
    assert real_disabled.json()["ready"] is False
    assert real_disabled.json()["reason"] == "REAL_API_CODING_AGENT_DISABLED"
    assert router2.requests == []


def test_status_route_reports_model_router_unavailable_without_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, real=True)
    router = StubModelRouter()
    rig = make_route_rig(tmp_path)
    rig.api.app.state.model_router_service = router

    response = rig.api.get(STATUS_ROUTE, params={"provider_id": "openrouter"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["ready"] is False
    assert payload["reason"] == "MODEL_ROUTER_UNAVAILABLE"
    assert payload["model_router_available"] is False
    assert router.requests == []


def test_status_route_reports_unconfigured_provider_without_key_or_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, real=True)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    rig = make_route_rig(tmp_path)

    response = rig.api.get(STATUS_ROUTE, params={"provider_id": "openrouter"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["ready"] is False
    assert payload["reason"] == "MODEL_PROVIDER_NOT_CONFIGURED"
    assert payload["provider_id"] == "openrouter"
    assert "Traceback" not in response.text
    assert "api_key" not in response.text.casefold()


def test_environment_key_does_not_bypass_canonical_connection_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, real=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-leaked")
    rig = make_route_rig(tmp_path)

    response = rig.api.get(STATUS_ROUTE, params={"provider_id": "openrouter"})
    payload = response.json()

    assert response.status_code == 200
    assert payload["ready"] is False
    assert payload["reason"] == "MODEL_PROVIDER_NOT_CONFIGURED"
    assert payload["credential_status"] == "credential_missing"
    assert payload["authentication_status"] == "auth_unknown"
    assert "sk-test-not-leaked" not in response.text

def test_status_route_handles_provider_model_query_params_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, real=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-leaked")
    rig = make_route_rig(tmp_path)
    connections = rig.api.app.state.connection_registry
    connections.connect("openrouter", api_key="stored-test-key", enabled=True)
    connections.record_transport_result("openrouter", {"ok": True, "status": "ready"})

    ready = rig.api.get(STATUS_ROUTE, params={"provider_id": "openrouter", "model": "openai/gpt-4o-mini"})
    missing_model = rig.api.get(STATUS_ROUTE, params={"provider_id": "openrouter", "model": "missing-model"})

    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["provider_id"] == "openrouter"
    assert ready.json()["model"] == "openai/gpt-4o-mini"
    assert missing_model.status_code == 200
    assert missing_model.json()["ready"] is False
    assert missing_model.json()["reason"] == "MODEL_NOT_AVAILABLE"
