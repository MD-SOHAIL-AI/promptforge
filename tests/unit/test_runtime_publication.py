"""Committed views and slow durability must not leak partial runtime state."""
import asyncio
import threading
import time
from pathlib import Path

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from tests.unit.test_agent_orchestrator_v2 import _changes, _providers


def test_atomic_journal_and_job_are_invisible_until_commit_and_rollback_notifies_nobody(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    store.save_run("r", "s", "p", "running", {"run_id": "r", "project_id": "p", "status": "running"})
    notifications = []
    store._observers.add(notifications.append)
    with pytest.raises(RuntimeError, match="rollback"):
        with store.atomic():
            store.append_event("s", {"event_type": "runtime.failed", "run_id": "r"},
                run_projection={"run_id": "r", "project_id": "p", "status": "failed"})
            assert notifications == []
            raise RuntimeError("rollback")
    assert store.load_run("r")["status"] == "running"
    assert store.events("s") == ()
    assert notifications == []
    with store.atomic():
        store.append_event("s", {"event_type": "runtime.completed", "run_id": "r"},
            run_projection={"run_id": "r", "project_id": "p", "status": "completed"})
        assert notifications == []
    assert notifications == ["s"]
    assert store.load_run("r")["status"] == "completed"
    assert store.verify_journal("s")["healthy"]


def test_public_snapshot_ignores_uncommitted_mutations_and_is_detached(tmp_path):
    owner = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
        store=AgentOrchestrationStore(tmp_path / "state.sqlite3"), run_factory=ProductAgentRun)
    run = ProductAgentRun("r", "p", "verified_template", status="running")
    owner._runs["r"] = run
    owner._nodes["r"] = {}
    owner._session_for_run["r"] = "s"
    job = owner.jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    run.job_id = job["job_id"]
    owner._persist_run(run)
    run.status = "blocked"
    run.classification = "AGENT_PROJECT_TRUST_REQUIRED"
    before = owner.run_snapshot("r")
    assert before["status"] == "running"
    assert before["classification"] is None
    owner._event(run, "runtime.blocked", classification=run.classification)
    after = owner.run_snapshot("r")
    assert after["status"] == after["job"]["status"] == "blocked"
    assert after["job"]["blocking_reason"] == "AGENT_PROJECT_TRUST_REQUIRED"
    after["job"]["blocking_reason"] = "modified by caller"
    assert owner.run_snapshot("r")["job"]["blocking_reason"] == "AGENT_PROJECT_TRUST_REQUIRED"


def test_caught_savepoint_failure_does_not_publish_rolled_back_session(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    notifications = []
    store._observers.add(notifications.append)
    with store.atomic():
        store.append_event("kept", {"event_type": "tool.completed"})
        with pytest.raises(RuntimeError, match="rollback"):
            with store.atomic():
                store.append_event("discarded", {"event_type": "tool.failed"})
                raise RuntimeError("rollback")
        assert notifications == []
    assert notifications == ["kept"]
    assert store.events("discarded") == ()
    assert len(store.events("kept")) == 1


@pytest.mark.parametrize("mismatch", ["project", "workspace", "session"])
def test_generated_session_binding_does_not_admit_foreign_contracts(tmp_path, mismatch):
    from backend.agent_runtime.task_contract import create_task_contract
    owner = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
        store=AgentOrchestrationStore(tmp_path / "state.sqlite3"), run_factory=ProductAgentRun)
    contract = create_task_contract("repair firmware", project_id="p", session_id=None,
        workspace=tmp_path, intent="generate")
    contract[{"project": "project_id", "workspace": "workspace_root", "session": "session_id"}[mismatch]] = "foreign"
    async def scenario():
        with pytest.raises(ValueError, match="TASK_WORKSPACE_BINDING_MISMATCH"):
            await owner.start_run(project_id="p", active_workspace_root=tmp_path, instruction="repair firmware",
                provider_id="verified_template", autonomy="build_only", active_workspace={"task_contract": contract})
        assert owner.store.list_runs() == ()
        assert owner.jobs.list() == ()
        await owner.close()
    asyncio.run(scenario())


def test_slow_progress_commit_yields_the_loop_and_preserves_order(tmp_path, monkeypatch):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    owner = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
        store=store, run_factory=ProductAgentRun)
    run = ProductAgentRun("r", "p", "verified_template", status="running")
    owner._runs["r"] = run
    owner._nodes["r"] = {}
    owner._session_for_run["r"] = "s"
    owner._recovery_context["r"] = {}
    run.job_id = owner.jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")["job_id"]
    owner._persist_run(run)
    entered = threading.Event()
    original = store.append_event
    def delayed(*args, **kwargs):
        entered.set()
        time.sleep(.2)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "append_event", delayed)
    async def scenario():
        commit = asyncio.create_task(owner._runtime_event_async(run,
            {"event_type": "assistant.summary", "message": "working"}))
        assert await asyncio.to_thread(entered.wait, 2)
        started = time.monotonic()
        await asyncio.sleep(.01)
        assert time.monotonic() - started < .1
        assert owner.run_snapshot("r")["status"] == "running"
        await commit
        assert [event["event_type"] for event in store.events("s")] == ["assistant.summary"]
        assert run.events[-1]["sequence"] == 1
    asyncio.run(scenario())


def test_cancelled_progress_commit_finishes_before_terminal_cleanup(tmp_path, monkeypatch):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    owner = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
        store=store, run_factory=ProductAgentRun)
    run = ProductAgentRun("r", "p", "verified_template", status="running")
    owner._runs["r"] = run
    owner._nodes["r"] = {}
    owner._session_for_run["r"] = "s"
    owner._recovery_context["r"] = {}
    run.job_id = owner.jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")["job_id"]
    owner._persist_run(run)
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()
    original = store.append_event
    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        result = original(*args, **kwargs)
        committed.set()
        return result
    monkeypatch.setattr(store, "append_event", delayed)
    async def scenario():
        task = asyncio.create_task(owner._runtime_event_async(run,
            {"event_type": "assistant.summary", "message": "working"}))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(.01)
        assert not task.done() and not committed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert committed.is_set()
        run.status = "cancelled"
        owner._event(run, "runtime.cancelled")
        assert [event["event_type"] for event in store.events("s")] == ["assistant.summary", "runtime.cancelled"]
        snapshot = owner.run_snapshot("r")
        assert snapshot["status"] == snapshot["job"]["status"] == "cancelled"
        assert store.verify_journal("s")["healthy"]
    asyncio.run(scenario())
