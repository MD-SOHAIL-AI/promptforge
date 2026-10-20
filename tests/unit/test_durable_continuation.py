import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


@pytest.mark.parametrize("binding", ["named", "generated", "legacy_generated"])
@pytest.mark.parametrize("failure", ["BUILD_ERROR", "AGENT_VERIFICATION_RESERVE_REACHED"])
@pytest.mark.parametrize("controller_version", [1, 2])
def test_resume_after_restart_is_single_linked_continuation(tmp_path, binding, failure, controller_version):
    from backend.agent_runtime.task_contract import create_task_contract
    async def scenario():
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("original")
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
        attempts = []
        async def executor(request):
            attempts.append(request.run_id)
            stage = Path(request.active_workspace_root)
            if len(attempts) == 1:
                (stage / "main.cpp").write_text("saved edit")
                await request.progress_callback("turn.settled", {
                    "turn": 3, "tool_count": 2,
                    "_resume_checkpoint": {"turn": 3, "tool_calls_used": 2, "elapsed_seconds": 4,
                        "observations": [{"call_id": "write-1", "tool": "write_file", "status": "completed", "output": {"path": "main.cpp"}}],
                        "plan": [{"id": "verify", "status": "pending", "title": "Build saved work"}],
                        "generation_controller": {"version": controller_version, "stalled_turns": 3,
                            "active_key": "display", "dirty_paths": ["main.cpp"],
                            "invalidated": {"display": ["old-write"]}},
                        "deleted_paths": [], "authorized_paths": ["main.cpp"],
                        "provider_budget": {"requests": 3, "output_tokens": 12288, "input_bytes": 1200}},
                })
                return {"success": False, "classification": failure, "message": "Interrupted validation", "build_verified": False}
            assert stage.joinpath("main.cpp").read_text() == "saved edit"
            checkpoint = request.active_workspace["agent_resume_checkpoint"]
            assert checkpoint["turn"] == 3
            assert checkpoint["observations"][0]["call_id"] == "write-1"
            assert checkpoint["provider_budget"]["requests"] == 3
            assert checkpoint["generation_controller"]["stalled_turns"] == 3
            assert checkpoint["generation_controller"]["active_key"] == "display"
            assert checkpoint["generation_controller"]["invalidated"] == {"display": ["old-write"]}
            assert stage.name == "workflow-" + request.run_id.removeprefix("agent-run-")
            job = current_owner.jobs.get(current_owner._runs[request.run_id].job_id)
            assert job["paused_by"] is None and job["status"] in {"queued", "running"}
            used = current_owner.jobs.reserve_provider_attempt(job["job_id"], request.run_id,
                {"requests": 1, "output_tokens": 128, "input_bytes": 10, "_attempt_id": "continued-provider-attempt"})
            assert used["requests"] == 4
            current_owner.jobs.settle_provider_attempt(job["job_id"], "continued-provider-attempt", 100)
            return {"success": True, "classification": "AGENT_V3_COMPLETED", "message": "Revalidated", "build_verified": True, "build_result": {"success": True}}
        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(store.path), run_factory=ProductAgentRun, autonomous_executor=executor)
        original = owner()
        current_owner = original
        session_id = "s" if binding == "named" else None
        contract = create_task_contract("repair firmware", project_id="p", session_id=session_id,
            workspace=workspace, intent="generate")
        run = await original.start_run(project_id="p", active_workspace_root=workspace, instruction="repair firmware", provider_id="verified_template", autonomy="build_only", session_id=session_id,
            active_workspace={"task_contract": contract})
        await original._tasks[run.run_id]
        session_key = session_id or f"run:{run.run_id}"
        assert run.task_contract["session_id"] == session_key
        assert contract["session_id"] == session_id  # Admission does not mutate its input.
        if binding == "legacy_generated":
            original._recovery_context[run.run_id]["active_workspace"]["task_contract"] = contract
            run.task_contract = contract
            original._persist_run(run)
        checkpoint_id = original.recovery_info(run.run_id)["checkpoint_id"]
        assert original.recovery_info(run.run_id)["eligible"] is (controller_version == 1)
        await original.close()
        restarted = owner()
        current_owner = restarted
        try:
            if controller_version != 1:
                info = restarted.recovery_info(run.run_id)
                assert info["actions"]["inspect"]["allowed"]
                assert info["actions"]["continue_generation"]["blocking_reason"] == "AGENT_CONTROLLER_VERSION_UNSUPPORTED"
                with pytest.raises(ValueError, match="CONTROLLER_VERSION_UNSUPPORTED"):
                    await restarted.resume(run.run_id, checkpoint_id=checkpoint_id, idempotency_key="future-controller")
                assert len(attempts) == 1
                return
            replacements = await asyncio.gather(*(restarted.resume(run.run_id, checkpoint_id=checkpoint_id, idempotency_key="resume-test-0001") for _ in range(4)))
            assert len({item.run_id for item in replacements}) == 1
            replacement = replacements[0]
            await restarted._tasks[replacement.run_id]
            assert replacement.retry_of_run_id == run.run_id
            assert replacement.status == "completed"
            assert replacement.task_contract["session_id"] == session_key
            assert len(attempts) == 2
            assert workspace.joinpath("main.cpp").read_text() == "original"
            assert len(restarted.store.list_runs(session_id=session_key)) == 2
        finally:
            await restarted.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["MODEL_OUTPUT_TRUNCATED", "MODEL_RETIRED", "PROVIDER_REQUEST_FAILED"])
def test_live_failure_recovery_preserves_draft_contract_and_output_hint(tmp_path, failure):
    from backend.agent_runtime.task_contract import create_task_contract
    from tests.unit.test_live_failure_fixes import SPEC
    async def scenario():
        retired = failure != "MODEL_OUTPUT_TRUNCATED"
        workspace = tmp_path / "project"
        workspace.mkdir()
        (workspace / "main.cpp").write_text("original")
        contract = create_task_contract("Keep modules small.", project_id="p", session_id="s", workspace=workspace, intent="generate")
        contract.pop("partition_version")
        contract["exclusions"] = [SPEC]
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
        calls = []
        async def executor(request):
            calls.append(request.run_id)
            stage = Path(request.active_workspace_root)
            if len(calls) == 1:
                (stage / "main.cpp").write_text("saved partial firmware")
                await request.progress_callback("turn.settled", {"turn": 1, "_resume_checkpoint": {
                    "turn": 1, "observations": [], "plan": [], "tool_calls_used": 1, "elapsed_seconds": 1}})
                return {"success": False, "classification": failure, "message": "model stopped",
                    "provider_diagnostics": {"http_status": 410} if failure == "PROVIDER_REQUEST_FAILED" else {},
                    "model_output_recovery": {"output_floor": 16384, "prefer_small_actions": True}}
            assert stage.joinpath("main.cpp").read_text() == "saved partial firmware"
            active = request.active_workspace
            recovered = "\n".join(item["text"] for item in active["task_contract"]["requirements"])
            assert "README.md" in recovered and "Compile" in recovered
            assert active["task_contract"]["exclusions"] == ["Do not flash or open serial."]
            hint = active["agent_output_recovery"]
            assert hint["output_floor"] == (8192 if retired else 16384)
            assert hint["prefer_small_actions"] is (not retired)
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "fixture endpoint unavailable"}
        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(store.path), run_factory=ProductAgentRun, autonomous_executor=executor)
        initial = owner()
        initial.auto_recovery_enabled = False
        run = await initial.start_run(project_id="p", active_workspace_root=workspace, instruction=SPEC,
            provider_id="fake_planner", autonomy="build_only", session_id="s", active_workspace={"task_contract": contract})
        await initial._tasks[run.run_id]
        initial.jobs.mutate(run.job_id, lambda job: job["consumed"].update(requests=7, output_tokens=1234))
        info = initial.recovery_info(run.run_id)
        assert info["eligible"] and run.recoverable
        await initial.close()
        restarted = owner()
        restarted.auto_recovery_enabled = False
        try:
            kwargs = {}
            if retired:
                assert restarted.get_run(run.run_id).classification == "MODEL_RETIRED"
                with pytest.raises(ValueError, match="MODEL_RETIRED"):
                    await restarted.resume(run.run_id, checkpoint_id=info["checkpoint_id"], idempotency_key="same-model")
                kwargs = {"provider_id": "ollama", "model_id": "selected-model"}
            replacement = await restarted.resume(run.run_id, checkpoint_id=info["checkpoint_id"],
                idempotency_key="continue", **kwargs)
            await restarted._tasks[replacement.run_id]
            assert len(calls) == 2 and replacement.job_id == run.job_id
            budget = restarted.jobs.get(run.job_id)["consumed"]
            assert budget["requests"] == 7 and budget["output_tokens"] == 1234
            assert workspace.joinpath("main.cpp").read_text() == "original"
        finally:
            await restarted.close()
    asyncio.run(scenario())


def test_checkpoint_seal_rejects_tampering(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "journal.sqlite3")
    event = store.append_event("s", {"event_type": "turn.settled", "run_id": "r", "turn": 1})
    saved = store.save_run_checkpoint("r", "s", turn=1, session_seq=event["sequence"], payload={"resume": {"turn": 1}})
    assert len(saved["checkpoint_id"]) == 64
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("UPDATE agent_run_checkpoints SET payload_json=replace(payload_json, '\"turn\":1', '\"turn\":2')")
        connection.commit()
    with pytest.raises(ValueError, match="CHECKPOINT_CORRUPT"):
        store.latest_run_checkpoint("r")


def test_claimed_guidance_is_requeued_until_acknowledged(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "queue.sqlite3")
    store.enqueue_input("s", "r", mode="STEER", content="keep the pin mapping")
    store.enqueue_input("s", None, mode="STEER", content="use ESP32")
    assert len(store.claim_queue_items("s", run_id="r")) == 2
    store.transfer_unacknowledged_guidance("r", "r2")
    assert len(store.claim_queue_items("s", run_id="r2")) == 2
    store.acknowledge_guidance("r2")
    store.transfer_unacknowledged_guidance("r2", "r3")
    assert store.claim_queue_items("s", run_id="r3") == ()


def test_effect_intent_cannot_be_replayed_after_restart(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "operations.sqlite3")
    payload = {"port": "COM7", "artifact_hash": "ab" * 32}
    store.operation("flash-1", "r", "hardware.flash", status="prepared", payload=payload)
    store.operation("flash-1", "r", "hardware.flash", status="started", payload=payload)
    reopened = AgentOrchestrationStore(store.path)
    reopened.interrupt_operations("r")
    assert reopened.operations("r")[0]["status"] == "unknown"
    with pytest.raises(ValueError, match="ALREADY_SETTLED"):
        reopened.operation("flash-1", "r", "hardware.flash", status="started", payload=payload)
