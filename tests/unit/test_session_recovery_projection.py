from types import SimpleNamespace

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.session_run_projector import AgentSessionRunProjector
from backend.agent_runtime.session_store import AgentSessionStore


def test_recovery_completion_survives_stale_session_pointer_and_restart(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.db")
    session = store.create(project_id="p")
    store.update_run(session.session_id, run_id="original", status="paused_model_unavailable")
    journal = AgentOrchestrationStore(tmp_path / "runs.db")
    for run_id, parent, replacement, status in [
        ("original", None, "repair", "paused_model_unavailable"),
        ("repair", "original", "verification", "failed"),
        ("verification", "repair", None, "completed_with_warning"),
    ]:
        journal.save_run(run_id, session.session_id, "p", status, {
            "run_id": run_id, "session_id": session.session_id, "project_id": "p",
            "retry_of_run_id": parent, "recovered_by_run_id": replacement, "status": status,
            "classification": "DRAFT_BUILD_VERIFIED" if run_id == "verification" else "",
            "assistant_message": "Saved draft compiled successfully." if run_id == "verification" else "Old failure",
        })
    projector = AgentSessionRunProjector(store, journal)
    restored = projector.reconcile(session.session_id)
    assert restored.active_run_id == "verification"
    assert restored.status == "completed_with_warning"
    assert restored.messages[-1].run_id == "verification"
    assert restored.messages[-1].metadata["classification"] == "DRAFT_BUILD_VERIFIED"
    projector.project(SimpleNamespace(to_safe_dict=lambda: journal.load_run("original")))
    again = AgentSessionRunProjector(AgentSessionStore(tmp_path / "sessions.db"), journal).reconcile(session.session_id)
    assert again.active_run_id == "verification"
    assert again.messages == restored.messages


def test_recovery_projection_cannot_follow_a_link_into_another_session(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.db")
    session = store.create(project_id="p")
    original = store.update_run(session.session_id, run_id="source", status="failed")
    journal = AgentOrchestrationStore(tmp_path / "runs.db")
    journal.save_run("source", session.session_id, "p", "failed", {
        "run_id": "source", "session_id": session.session_id, "project_id": "p",
        "recovered_by_run_id": "other", "status": "failed",
    })
    journal.save_run("other", "another-session", "p", "completed", {
        "run_id": "other", "session_id": "another-session", "project_id": "p",
        "retry_of_run_id": "source", "status": "completed",
    })
    assert AgentSessionRunProjector(store, journal).reconcile(session.session_id) == original


def test_recovery_projection_cannot_overwrite_a_newer_user_task(tmp_path, monkeypatch):
    store = AgentSessionStore(tmp_path / "sessions.db")
    session = store.create(project_id="p")
    store.update_run(session.session_id, run_id="source", status="failed")
    journal = AgentOrchestrationStore(tmp_path / "runs.db")
    for run_id, replacement, parent in [("source", "verified", None), ("verified", None, "source")]:
        journal.save_run(run_id, session.session_id, "p", "completed", {
            "run_id": run_id, "session_id": session.session_id, "project_id": "p",
            "recovered_by_run_id": replacement, "retry_of_run_id": parent, "status": "completed",
        })
    update = store.update_run
    def interleaved_update(session_id, **kwargs):
        update(session_id, run_id="new-task", status="running")
        return update(session_id, **kwargs)
    monkeypatch.setattr(store, "update_run", interleaved_update)
    result = AgentSessionRunProjector(store, journal).reconcile(session.session_id)
    assert result.active_run_id == "new-task"
    assert result.messages == ()
