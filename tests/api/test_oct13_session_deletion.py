"""Exercise deletion against isolated API sessions, never live user state."""
import pytest

from backend.agent_runtime.product_agent_service import ProductAgentRun
from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env


@pytest.mark.parametrize("status,expected", [("completed_with_warning", 200), ("completed", 200),
    ("cancelled", 200), ("queued", 409), ("running", 409), ("cancelling", 409),
    ("awaiting_flash_confirmation", 409), ("paused_model_unavailable", 200)])
def test_delete_uses_canonical_completion_and_retires_paused_recovery(tmp_path, monkeypatch, status, expected):
    setup_env(monkeypatch, tmp_path, enabled=True)
    client, project_id, _ = make_client(tmp_path)
    with client as api:
        services = api.app.state.services
        owner = services.product_agent_service.orchestrator
        owner.auto_recovery_enabled = False
        session = api.post("/agent-runtime/sessions", json={"project_id": project_id}).json()["session"]
        identity = session["session_id"]
        run = ProductAgentRun("fixture-run", project_id, "fake_planner", status=status,
            classification="PROVIDER_BUSY" if status == "paused_model_unavailable" else "fixture")
        owner._runs[run.run_id] = run
        owner._session_for_run[run.run_id] = identity
        owner._nodes[run.run_id] = {}
        owner._recovery_context[run.run_id] = {}
        services.agent_session_store.update_run(identity, run_id=run.run_id, status=status)
        response = api.delete(f"/agent-runtime/sessions/{identity}")
        assert response.status_code == expected, response.text
        if expected == 409:
            assert response.json()["code"] == "AGENT_SESSION_ACTIVE"
            assert api.get(f"/agent-runtime/sessions/{identity}").status_code == 200
            assert run.status == status
        else:
            assert api.get(f"/agent-runtime/sessions/{identity}").status_code == 404
            if status == "paused_model_unavailable":
                assert run.status == "cancelled"
                assert run.cancel_event.is_set()
