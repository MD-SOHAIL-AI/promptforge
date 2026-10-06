"""Exercise action permissions against sealed drafts and persisted job budgets."""
import asyncio
from pathlib import Path

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


@pytest.mark.parametrize("configuration_only", [False, True])
def test_recovery_actions_refresh_trust_without_resetting_the_job(tmp_path, configuration_only):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        async def executor(request):
            stage = Path(request.active_workspace_root)
            (stage / "platformio.ini").write_text("[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n")
            if not configuration_only:
                (stage / "main.cpp").write_text("int draft;\n")
            await request.progress_callback("turn.settled", {"turn": 1, "_resume_checkpoint": {
                "turn": 1, "observations": [], "plan": [], "tool_calls_used": 0, "elapsed_seconds": 0}})
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "model stalled",
                "provider_diagnostics": {"physical_attempt_id":"physical-1", "response_headers_received":True,
                    "first_event_ms":20, "last_event_ms":30, "stream_event_count":2,
                    "completion_received":False, "timeout_stage":"stream_idle", "duration_ms":60030}}
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages")
        store = AgentOrchestrationStore(tmp_path / "journal.sqlite3")
        owner = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path), store=store,
            run_factory=ProductAgentRun, autonomous_executor=executor)
        owner.auto_recovery_enabled = False
        try:
            run = await owner.start_run(project_id="p", active_workspace_root=workspace, instruction="build firmware",
                provider_id="verified_template", autonomy="build_only", session_id="s")
            await owner._tasks[run.run_id]
            info = owner.recovery_info(run.run_id)
            assert info["actions"]["inspect"]["allowed"]
            assert info["actions"]["continue_generation"]["allowed"]
            assert info["actions"]["verify_locally"] == {"allowed":False, "blocking_reason":"AGENT_PROJECT_TRUST_REQUIRED"}
            owner.jobs.mutate(run.job_id, lambda job: job.update(status="blocked", blocking_reason="AGENT_PROJECT_TRUST_REQUIRED"))
            before = owner.jobs.get(run.job_id)
            owner.jobs.trust("p", str(workspace.resolve()), True)
            owner.project_trust_changed("p")
            after = owner.jobs.get(run.job_id)
            assert after["blocking_reason"] is None
            for key in ("consumed", "limits", "auto_recovery_count", "outage_started_at", "current_run_id"):
                assert before.get(key) == after.get(key)
            info = owner.recovery_info(run.run_id)
            assert info["actions"]["continue_generation"]["allowed"]
            assert info["actions"]["verify_locally"]["allowed"] is (not configuration_only)
            assert any(event["event_type"] == "job.permissions.changed" for event in run.events)
            if configuration_only:
                with pytest.raises(ValueError, match="DRAFT_INCOMPLETE"):
                    await owner.verify_saved_draft(run.run_id, idempotency_key="incomplete-draft")
            else:
                # Model allowance is irrelevant to a local build. Local tool/time
                # limits remain enforced; inspecting the draft stays possible.
                for dimension in ("requests", "output_tokens", "input_bytes"):
                    owner.jobs.mutate(run.job_id, lambda job, key=dimension: job["consumed"].update({key:job["limits"][key]}))
                info = owner.recovery_info(run.run_id)
                assert not info["actions"]["continue_generation"]["allowed"]
                assert info["actions"]["verify_locally"]["allowed"]
                owner.jobs.mutate(run.job_id, lambda job: job["consumed"].update(tool_calls_used=job["limits"]["tool_calls_used"]))
                info = owner.recovery_info(run.run_id)
                assert info["actions"]["inspect"]["allowed"]
                assert info["actions"]["verify_locally"]["blocking_reason"] == "AGENT_JOB_BUDGET_EXHAUSTED"
                with pytest.raises(ValueError, match="BUDGET_EXHAUSTED"):
                    await owner.verify_saved_draft(run.run_id, idempotency_key="exhausted-local-budget")
            assert run.to_safe_dict()["provider_diagnostics"]["timeout_stage"] == "stream_idle"
        finally:
            await owner.close()
        restarted = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path), store=store,
            run_factory=ProductAgentRun, autonomous_executor=executor)
        restarted.auto_recovery_enabled = False
        try:
            diagnostics = restarted.get_run(run.run_id).to_safe_dict()["provider_diagnostics"]
            assert diagnostics["physical_attempt_id"] == "physical-1"
            assert diagnostics["first_event_ms"] == 20
            assert diagnostics["timeout_stage"] == "stream_idle"
        finally:
            await restarted.close()
    asyncio.run(scenario())
