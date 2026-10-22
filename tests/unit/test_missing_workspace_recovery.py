"""Missing external folders must not consume recovery or destroy saved work."""
import asyncio
import logging
from pathlib import Path

import pytest

from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


def seed(tmp_path, status):
    workspace = tmp_path / "external-project"
    workspace.mkdir()
    (workspace / "main.cpp").write_text("original source")
    changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages")
    stage = changes.create_stage("saved-boundary")
    (stage / "main.cpp").write_text("saved edit")
    store = AgentOrchestrationStore(tmp_path / "journal.sqlite3")
    run = ProductAgentRun("saved-run", "p", "fake_planner", status=status, autonomy="build_only",
        classification="PROVIDER_UNAVAILABLE" if status == "paused_model_unavailable" else None,
        recoverable=status != "cancelled")
    jobs = AgentJobStore(store)
    job = jobs.create(project_id="p", session_id="s", workspace_root=workspace, run_id=run.run_id)
    run.job_id = job["job_id"]
    boundary = store.append_event("s", {"event_type": "turn.settled", "run_id": run.run_id, "turn": 1})
    checkpoint = store.save_run_checkpoint(run.run_id, "s", turn=1, session_seq=boundary["sequence"], payload={"resume": {
        "turn": 1, "stage_path": str(stage), "workspace_revision": changes.inspect_workspace(workspace).revision,
        "stage_revision": changes.inspect_workspace(stage).revision, "stage_mode": "clone",
        "authorized_paths": ["main.cpp"], "deleted_paths": [], "observations": [], "plan": [],
        "tool_calls_used": 2, "elapsed_seconds": 42,
        "provider_budget": {"requests": 7, "output_tokens": 1000, "input_bytes": 100}}})
    jobs.update_run(job["job_id"], run, checkpoint)
    jobs.trust("p", str(workspace), True)
    payload = {**run.to_safe_dict(), "session_id": "s", "_recovery_context": {
        "workspace_root": str(workspace), "instruction": "build firmware", "active_workspace": {},
        "explicit_edit_authorized": True}}
    store.save_run(run.run_id, "s", "p", status, payload)
    return workspace, changes, store, run, checkpoint


@pytest.mark.parametrize("status", ["cancelled", "running", "paused_model_unavailable"])
def test_missing_folder_survives_restarts_and_can_resume_after_exact_restore(tmp_path, caplog, status):
    async def scenario():
        workspace, changes, store, saved, checkpoint = seed(tmp_path, status)
        relocated = tmp_path / "relocated-project"
        assert workspace.resolve().parent == relocated.resolve().parent == tmp_path.resolve()
        workspace.rename(relocated)
        attempts = []
        async def executor(request):
            attempts.append(request.run_id)
            assert Path(request.active_workspace_root, "main.cpp").read_text() == "saved edit"
            return {"success": False, "classification": "PROVIDER_UNAVAILABLE", "message": "test outage"}
        def owner():
            return AgentOrchestrator(change_service=changes, store=AgentOrchestrationStore(store.path),
                provider_registry=_providers(tmp_path), run_factory=ProductAgentRun, autonomous_executor=executor)
        for _ in range(2):
            current = owner()
            try:
                run = current.get_run(saved.run_id)
                current._retain_sealed_checkpoint(run)
                job = current.jobs.get(run.job_id)
                assert job["blocking_reason"] == "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
                assert job["retry_due_at"] is None
                assert job["consumed"]["requests"] == 7
                assert job["consumed"]["elapsed_seconds"] == 42
                assert job["auto_recovery_count"] == 0
                assert not current.recovery_info(run.run_id)["eligible"]
                if status != "cancelled":
                    assert current.recovery_info(run.run_id)["blocking_reason"] == "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
                    assert job["status"] == "blocked"
                    with pytest.raises(ValueError, match="AGENT_RESUME_WORKSPACE_UNAVAILABLE"):
                        await current.retry(run.run_id)
                await current.recover_jobs()
                current._schedule_retry(job["job_id"])
                assert not current._retry_tasks and not attempts
                assert current.store.recovery_replacement(run.run_id) is None
                assert current.store.latest_run_checkpoint(run.run_id) == checkpoint
                assert Path(checkpoint["resume"]["stage_path"], "main.cpp").read_text() == "saved edit"
                assert not workspace.exists()
            finally:
                await current.close()
        relocated.rename(workspace)
        restored = owner()
        try:
            run = restored.get_run(saved.run_id)
            assert restored.recovery_info(run.run_id)["eligible"]
            replacement = await restored.resume_job(run.job_id, idempotency_key="restore-once",
                checkpoint_id=checkpoint["checkpoint_id"])
            await restored._tasks[replacement.run_id]
            assert attempts == [replacement.run_id]
            assert restored.jobs.get(run.job_id)["consumed"]["requests"] == 7
            assert workspace.joinpath("main.cpp").read_text() == "original source"
        finally:
            await restored.close()
    with caplog.at_level(logging.INFO, logger="backend.agent_runtime.orchestrator"):
        asyncio.run(scenario())
    assert "Cannot retain sealed recovery draft" not in caplog.text
    assert sum("Recovery paused: project folder unavailable" in record.message for record in caplog.records) == 1


def test_missing_stage_has_a_different_reason_from_missing_project(tmp_path):
    workspace, changes, store, saved, checkpoint = seed(tmp_path, "paused_model_unavailable")
    stage = Path(checkpoint["resume"]["stage_path"])
    assert stage.resolve().parent == changes.staging_root.resolve()
    stage.rename(changes.staging_root / "relocated-seal")
    owner = AgentOrchestrator(change_service=changes, store=store, provider_registry=_providers(tmp_path), run_factory=ProductAgentRun)
    try:
        assert owner.recovery_info(saved.run_id)["blocking_reason"] == "AGENT_RESUME_STAGE_UNAVAILABLE"
        assert workspace.is_dir()
    finally:
        asyncio.run(owner.close())
