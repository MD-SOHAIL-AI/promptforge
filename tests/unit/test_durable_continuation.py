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


def test_resume_after_restart_is_single_linked_continuation(tmp_path):
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
                        "deleted_paths": [], "authorized_paths": ["main.cpp"],
                        "provider_budget": {"requests": 3, "output_tokens": 12288, "input_bytes": 1200}},
                })
                return {"success": False, "classification": "BUILD_ERROR", "message": "Interrupted validation", "build_verified": False}
            assert stage.joinpath("main.cpp").read_text() == "saved edit"
            checkpoint = request.active_workspace["agent_resume_checkpoint"]
            assert checkpoint["turn"] == 3
            assert checkpoint["observations"][0]["call_id"] == "write-1"
            assert checkpoint["provider_budget"]["requests"] == 3
            return {"success": True, "classification": "AGENT_V3_COMPLETED", "message": "Revalidated", "build_verified": True, "build_result": {"success": True}}
        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(store.path), run_factory=ProductAgentRun, autonomous_executor=executor)
        original = owner()
        run = await original.start_run(project_id="p", active_workspace_root=workspace, instruction="repair firmware", provider_id="verified_template", autonomy="build_only", session_id="s")
        await original._tasks[run.run_id]
        checkpoint_id = original.recovery_info(run.run_id)["checkpoint_id"]
        assert original.recovery_info(run.run_id)["eligible"]
        await original.close()
        restarted = owner()
        try:
            replacements = await asyncio.gather(*(restarted.resume(run.run_id, checkpoint_id=checkpoint_id, idempotency_key="resume-test-0001") for _ in range(4)))
            assert len({item.run_id for item in replacements}) == 1
            replacement = replacements[0]
            await restarted._tasks[replacement.run_id]
            assert replacement.retry_of_run_id == run.run_id
            assert replacement.status == "completed"
            assert len(attempts) == 2
            assert workspace.joinpath("main.cpp").read_text() == "original"
            assert len(restarted.store.list_runs(session_id="s")) == 2
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
