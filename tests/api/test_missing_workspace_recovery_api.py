from pathlib import Path

from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env, wait_run_status


def test_missing_project_resume_returns_conflict_without_replacement_or_model_request(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    api, project_id, _ = make_client(tmp_path)
    with api:
        owner = api.app.state.services.product_agent_service.orchestrator
        owner.auto_recovery_enabled = False
        async def executor(request):
            return {"success": False, "classification": "PROVIDER_UNAVAILABLE", "message": "test outage"}
        monkeypatch.setattr(owner, "autonomous_executor", executor)
        started = api.post("/agent-runtime/runs", json={"project_id": project_id, "provider_id": "fake_planner",
            "instruction": "build firmware", "autonomy": "build_only"})
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"paused_model_unavailable"})
        checkpoint = owner.store.latest_run_checkpoint(run["run_id"])
        workspace = Path(owner._recovery_context[run["run_id"]]["workspace_root"])
        destination = workspace.with_name(workspace.name + "-relocated")
        assert workspace.resolve().is_relative_to(tmp_path.resolve()) and destination.resolve().is_relative_to(tmp_path.resolve())
        workspace.rename(destination)
        def no_dispatch(*args, **kwargs):
            raise AssertionError("missing workspace must not contact a model or start another run")
        monkeypatch.setattr(owner, "start_run", no_dispatch)
        path = "/agent-runtime/jobs/" + run["job_id"]
        projection = api.get(path)
        assert projection.status_code == 200, projection.text
        assert projection.json()["recovery"]["blocking_reason"] == "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
        resumed = api.post(path + "/resume", json={"idempotency_key": "missing-workspace-once",
            "checkpoint_id": checkpoint["checkpoint_id"]})
        assert resumed.status_code == 409, resumed.text
        assert resumed.json()["code"] == "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
        assert owner.store.recovery_replacement(run["run_id"]) is None
        assert owner.store.latest_run_checkpoint(run["run_id"]) == checkpoint
        assert not workspace.exists()
