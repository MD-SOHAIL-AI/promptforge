import asyncio
from pathlib import Path

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


def test_every_continuation_can_resume_before_its_first_settled_turn(tmp_path):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("original")
        owner = AgentOrchestrator(
            change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
            provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"),
            run_factory=ProductAgentRun,
            autonomous_executor=lambda request: unavailable(request))
        owner.auto_recovery_enabled = False
        async def unavailable(request):
            assert Path(request.active_workspace_root, "main.cpp").read_text() == "original"
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}
        try:
            run = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
            for attempt in range(4):
                await owner._tasks[run.run_id]
                info = owner.recovery_info(run.run_id)
                assert info["eligible"], info
                assert owner.store.latest_run_checkpoint(run.run_id)["resume"]["turn"] == 0
                if attempt < 3:
                    run = await owner.resume(run.run_id, checkpoint_id=info["checkpoint_id"],
                        idempotency_key=f"resume-attempt-{attempt}")
            assert len(owner.jobs.get(run.job_id)["run_ids"]) == 4
            assert workspace.joinpath("main.cpp").read_text() == "original"
        finally:
            await owner.close()
    asyncio.run(scenario())


def test_resumed_nonzero_turn_is_sealed_as_admission_without_losing_saved_work(tmp_path):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("original")
        attempts = 0
        async def executor(request):
            nonlocal attempts
            attempts += 1
            stage = Path(request.active_workspace_root)
            if attempts == 1:
                (stage / "main.cpp").write_text("saved edit")
                await request.progress_callback("turn.settled", {"turn": 3, "_resume_checkpoint": {
                    "turn": 3, "observations": [], "plan": [], "tool_calls_used": 2,
                    "elapsed_seconds": 4, "authorized_paths": ["main.cpp"]}})
            else:
                assert request.active_workspace["agent_resume_checkpoint"]["turn"] == 3
                assert (stage / "main.cpp").read_text() == "saved edit"
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}
        owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
            provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor)
        owner.auto_recovery_enabled = False
        try:
            run = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
            for attempt in range(3):
                await owner._tasks[run.run_id]
                checkpoint = owner.store.latest_run_checkpoint(run.run_id)
                assert checkpoint["resume"]["turn"] == 3
                if attempt:
                    assert checkpoint["turn"] == 0
                    assert checkpoint["safe_boundary"] == "runtime.admitted"
                info = owner.recovery_info(run.run_id)
                assert info["eligible"] and info["checkpoint_id"] == checkpoint["checkpoint_id"]
                if attempt < 2:
                    run = await owner.resume(run.run_id, checkpoint_id=info["checkpoint_id"],
                        idempotency_key=f"nonzero-resume-{attempt}")
            assert workspace.joinpath("main.cpp").read_text() == "original"
        finally:
            await owner.close()
    asyncio.run(scenario())


def test_explicit_model_change_keeps_checkpoint_job_and_consumed_budget(tmp_path):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        providers = _providers(tmp_path)
        providers._model_provider_registry.configure_provider("ollama", {"enabled": True, "default_model": "local-model"})
        async def unavailable(request):
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}
        owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
            provider_registry=providers, store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=unavailable)
        owner.auto_recovery_enabled = False
        try:
            first = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
            await owner._tasks[first.run_id]
            owner.jobs.persist_provider_budget(first.job_id, {"requests": 7, "output_tokens": 4096, "input_bytes": 1000})
            checkpoint = owner.recovery_info(first.run_id)["checkpoint_id"]
            second = await owner.resume(first.run_id, checkpoint_id=checkpoint, idempotency_key="change-model-once",
                provider_id="ollama", model_id="local-model")
            assert second.provider_id == "ollama" and second.model_id == "local-model"
            assert second.job_id == first.job_id
            assert owner.jobs.get(second.job_id)["consumed"]["requests"] == 7
            replay = await owner.resume(first.run_id, checkpoint_id=checkpoint, idempotency_key="change-model-once",
                provider_id="ollama", model_id="local-model")
            assert replay.run_id == second.run_id
            with pytest.raises(ValueError):
                await owner.resume(first.run_id, checkpoint_id=checkpoint, idempotency_key="change-model-once",
                    provider_id="ollama", model_id="different-model")
            await owner._tasks[second.run_id]
        finally:
            await owner.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("settled_checkpoint", [True, False])
def test_local_verification_preserves_draft_and_does_not_spend_provider_budget(tmp_path, settled_checkpoint):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from tests.unit.test_agent_verification_freshness import Builder
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        builder = Builder()
        async def executor(request):
            stage = Path(request.active_workspace_root)
            if request.active_workspace.get("agent_verify_saved_draft"):
                assert (stage / "main.cpp").read_text() == "int draft;\n"
                job = owner.jobs.get(owner.get_run(request.run_id).job_id)
                loop = AutonomousForgeAgentLoop(workspace_root=stage, planner=None, platformio=builder,
                    progress=request.progress_callback, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True,
                    job_limits=job["limits"], job_consumed=job["consumed"])
                return (await loop.verify_saved_draft()).to_dict()
            (stage / "main.cpp").write_text("int draft;\n")
            if settled_checkpoint:
                await request.progress_callback("turn.settled", {"turn": 1, "_resume_checkpoint": {
                    "turn": 1, "observations": [], "plan": [], "tool_calls_used": 0, "elapsed_seconds": 0}})
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}
        owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
            provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor)
        owner.auto_recovery_enabled = False
        try:
            first = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
            await owner._tasks[first.run_id]
            with pytest.raises(ValueError, match="TRUST_REQUIRED"):
                await owner.verify_saved_draft(first.run_id, idempotency_key="verify-draft-once")
            owner.jobs.trust("p", str(workspace.resolve()), True)
            owner.jobs.persist_provider_budget(first.job_id, {"requests": 120, "output_tokens": 245760, "input_bytes": 8000000})
            second = await owner.verify_saved_draft(first.run_id, idempotency_key="verify-draft-once")
            replay = await owner.verify_saved_draft(first.run_id, idempotency_key="verify-draft-once")
            assert replay.run_id == second.run_id
            await owner._tasks[second.run_id]
            assert second.build_verified and second.status == "completed_with_warning"
            assert second.classification == "DRAFT_BUILD_VERIFIED"
            assert second.change_set_id and owner.change_service.get(second.change_set_id).status == "draft"
            assert second.to_safe_dict()["build_verified"]
            assert owner.jobs.get(second.job_id)["consumed"]["requests"] == 120
            assert builder.calls == 1 and not (workspace / "main.cpp").exists()
            owner.jobs.extend_budget(second.job_id, {"requests": 120}, "extend-for-model-retry")
            third = await owner.retry(second.run_id)
            await owner._tasks[third.run_id]
            assert not owner._recovery_context[third.run_id]["active_workspace"].get("agent_verify_saved_draft")
        finally:
            await owner.close()
    asyncio.run(scenario())


def test_legacy_continuation_uses_parent_seal_only_without_child_effects(tmp_path):
    from contextlib import closing
    import sqlite3
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        async def unavailable(request):
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}
        owner = AgentOrchestrator(
            change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
            provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=unavailable)
        owner.auto_recovery_enabled = False
        try:
            first = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
            await owner._tasks[first.run_id]
            parent_info = owner.recovery_info(first.run_id)
            child = await owner.resume(first.run_id, checkpoint_id=parent_info["checkpoint_id"], idempotency_key="legacy-resume-1")
            await owner._tasks[child.run_id]
            # Older projections did not atomically retain their own sealed
            # boundary. Simulate that format as well as its missing index.
            owner._recovery_context[child.run_id].pop("sealed_resume_boundary", None)
            with closing(sqlite3.connect(owner.store.path)) as connection:
                connection.execute("DELETE FROM agent_run_checkpoints WHERE run_id=?", (child.run_id,))
                connection.commit()
            info = owner.recovery_info(child.run_id)
            assert info["eligible"]
            assert info["checkpoint_id"] == parent_info["checkpoint_id"]
            owner.store.operation("uncertain", child.run_id, "hardware.flash", status="started", payload={"port": "COM7"})
            assert owner.recovery_info(child.run_id)["blocking_reason"] == "AGENT_RECOVERY_OUTCOME_UNKNOWN"
        finally:
            await owner.close()
    asyncio.run(scenario())


def test_admission_is_sealed_without_claiming_a_settled_turn(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "journal.sqlite3")
    event = store.append_event("s", {"event_type": "runtime.admitted", "run_id": "r"})
    saved = store.save_run_checkpoint("r", "s", turn=0, session_seq=event["sequence"], payload={"resume": {"turn": 0}})
    assert store.latest_run_checkpoint("r") == saved
    assert saved["safe_boundary"] == "runtime.admitted"
    assert saved["effect_replay_allowed"] is False
    with pytest.raises(ValueError, match="BOUNDARY_INVALID"):
        store.save_run_checkpoint("r", "s", turn=1, session_seq=event["sequence"], payload={})


def test_recovery_rebuilds_child_checkpoint_index_from_committed_projection(tmp_path):
    from contextlib import closing
    import sqlite3

    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages")
        database = tmp_path / "journal.sqlite3"

        async def unavailable(request):
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "overloaded"}

        def create_owner():
            result = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(database), run_factory=ProductAgentRun, autonomous_executor=unavailable)
            result.auto_recovery_enabled = False
            return result

        owner = create_owner()
        first = await owner.start_run(project_id="p", active_workspace_root=workspace,
            instruction="build firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
        await owner._tasks[first.run_id]
        parent = owner.recovery_info(first.run_id)
        child = await owner.resume(first.run_id, checkpoint_id=parent["checkpoint_id"], idempotency_key="child-index-rebuild")
        await owner._tasks[child.run_id]
        committed = owner.store.latest_run_checkpoint(child.run_id)
        await owner.close()
        # Simulate interruption after the journal/projection transaction but
        # before its derived checkpoint index was committed.
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("DELETE FROM agent_run_checkpoints WHERE run_id=?", (child.run_id,))
            connection.commit()
        reopened = create_owner()
        try:
            recovered = reopened.recovery_info(child.run_id)
            assert recovered["eligible"]
            rebuilt = reopened.store.latest_run_checkpoint(child.run_id)
            assert rebuilt["run_id"] == child.run_id
            assert rebuilt["resume"] == committed["resume"]
            assert rebuilt["safe_boundary"] == "runtime.admitted"
            assert recovered["checkpoint_id"] != parent["checkpoint_id"]
        finally:
            await reopened.close()

    asyncio.run(scenario())


def test_first_request_outage_resumes_admitted_work_after_restart(tmp_path):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("original")
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        database = tmp_path / "journal.sqlite3"
        attempts = []

        async def executor(request):
            attempts.append(request.run_id)
            if len(attempts) == 1:
                return {"success": False, "classification": "PROVIDER_UNAVAILABLE", "message": "Endpoint overloaded"}
            assert request.active_workspace["agent_resume_checkpoint"]["turn"] == 0
            assert Path(request.active_workspace_root, "main.cpp").read_text() == "original"
            return {"success": True, "classification": "AGENT_V3_COMPLETED", "build_verified": True,
                    "build_result": {"success": True}}

        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(database), run_factory=ProductAgentRun, autonomous_executor=executor)

        first = owner()
        first.auto_recovery_enabled = False
        run = await first.start_run(project_id="p", active_workspace_root=workspace, instruction="inspect firmware",
            provider_id="verified_template", autonomy="build_only", session_id="s")
        await first._tasks[run.run_id]
        assert run.status == "paused_model_unavailable"
        assert first.recovery_info(run.run_id)["eligible"]
        first.jobs.trust("p", str(workspace.resolve()), True)
        first.jobs.persist_provider_budget(run.job_id, {"requests": 4, "input_bytes": 100, "output_tokens": 4096})
        first.jobs.mutate(run.job_id, lambda job: job.update(retry_due_at="2020-01-01T00:00:00Z"))
        await first.close()
        reopened = owner()
        reopened.auto_recovery_enabled = True
        try:
            await reopened.recover_jobs()
            replacement_id = reopened.get_run(run.run_id).recovered_by_run_id
            assert replacement_id
            await reopened._tasks[replacement_id]
            replacement = reopened.get_run(replacement_id)
            assert replacement.status == "completed"
            assert replacement.provider_id == run.provider_id
            assert replacement.model_id == run.model_id
            assert reopened.jobs.get(run.job_id)["consumed"]["requests"] == 4
            assert attempts == [run.run_id, replacement_id]
            assert workspace.joinpath("main.cpp").read_text() == "original"
        finally:
            await reopened.close()
    asyncio.run(scenario())


def test_auto_recovery_stops_when_provider_outage_would_use_time_reserve(tmp_path):
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        database = tmp_path / "journal.sqlite3"
        attempts = []
        async def executor(request):
            attempts.append(request.run_id)
            return {"success": False, "classification": "PROVIDER_UNAVAILABLE", "message": "overloaded"}
        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(database), run_factory=ProductAgentRun,
                autonomous_executor=executor)
        first = owner()
        first.auto_recovery_enabled = False
        run = await first.start_run(project_id="p", active_workspace_root=workspace,
            instruction="inspect firmware", provider_id="verified_template",
            autonomy="build_only", session_id="s")
        await first._tasks[run.run_id]
        first.jobs.record_elapsed(run.job_id, 800)
        first.jobs.trust("p", str(workspace.resolve()), True)
        first.jobs.mutate(run.job_id, lambda job: job.update(retry_due_at="2020-01-01T00:00:00Z"))
        await first.close()
        reopened = owner()
        try:
            await reopened.recover_jobs()
            job = reopened.jobs.get(run.job_id)
            assert job["status"] == "paused"
            assert job["paused_by"] == "budget"
            assert job["blocking_reason"] == "AGENT_JOB_TIME_RESERVE_REQUIRED"
            assert job["retry_due_at"] is None
            assert attempts == [run.run_id]
        finally:
            await reopened.close()
    asyncio.run(scenario())


def test_waking_retry_can_schedule_its_successor(tmp_path):
    async def scenario():
        owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stage"),
            provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"), run_factory=ProductAgentRun)
        owner.auto_recovery_enabled = True
        job = owner.jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
        owner.jobs.mutate(job["job_id"], lambda value: value.update(retry_due_at="2020-01-01T00:00:00Z"))
        wakes = []
        async def recover(job_id=None):
            wakes.append(True)
            if len(wakes) == 1:
                owner._schedule_retry(job["job_id"])
        owner.recover_jobs = recover
        owner._schedule_retry(job["job_id"])
        first = owner._retry_tasks[job["job_id"]]
        await first
        await owner._retry_tasks[job["job_id"]]
        assert len(wakes) == 2
        await owner.close()
    asyncio.run(scenario())
