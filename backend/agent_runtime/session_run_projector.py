"""Project durable Agent run state into persistent user-facing sessions."""
from __future__ import annotations

from typing import Any, Mapping

from .session_store import AgentSession, AgentSessionStore
from .orchestration_store import AgentOrchestrationStore

STABLE_RUN_STATUSES = frozenset({
    "completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out",
    "awaiting_flash_confirmation", "paused_model_unavailable",
})


class AgentSessionRunProjector:
    """Synchronous idempotent projection invoked after orchestrator persistence."""

    def __init__(self, store: AgentSessionStore, journal: AgentOrchestrationStore | None = None) -> None:
        self.store = store
        self.journal = journal

    def reconcile(self, session_id: str) -> AgentSession:
        """Follow committed recovery links, including replacements missed during a crash."""
        session = self.store.get(session_id)
        if self.journal is None or session.kind != "agent" or not session.active_run_id:
            return session
        payload = self.journal.load_run(session.active_run_id)
        seen: set[str] = set()
        while payload:
            run_id = str(payload.get("run_id") or "")
            if run_id in seen:
                return session
            seen.add(run_id)
            if payload.get("session_id") != session_id or payload.get("project_id") != session.project_id:
                return session
            replacement_id = payload.get("recovered_by_run_id")
            replacement = self.journal.load_run(str(replacement_id)) if replacement_id else None
            if not replacement or replacement.get("retry_of_run_id") != run_id:
                break
            payload = replacement
        if payload:
            run_id = str(payload["run_id"])
            status = str(payload["status"])
            if session.active_run_id != run_id or session.status != status:
                updated = self.store.update_run(session_id, run_id=run_id, status=status,
                                                expected_run_id=session.active_run_id)
                if updated.active_run_id != run_id:
                    return updated
            self._project_session(session_id, payload)
        return self.store.get(session_id)

    def project(self, run: Any) -> None:
        safe = run.to_safe_dict()
        run_id = str(safe.get("run_id") or "")
        if not run_id:
            return
        sessions = tuple(self.store.list())
        for listed in sessions:
            if self.journal is not None and listed.project_id == safe.get("project_id"):
                listed = self.reconcile(listed.session_id)
            if listed.active_run_id != run_id:
                continue
            self._project_session(listed.session_id, safe)

    def _project_session(self, session_id: str, safe: Mapping[str, object]) -> None:
        status = str(safe.get("status") or "")
        run_id = str(safe.get("run_id") or "")
        try:
            session = self.store.get(session_id)
        except KeyError:
            return
        if session.active_run_id != run_id:
            return

        if str(safe.get("classification") or "") == "PLAN_READY":
            raw_plan = safe.get("agent_plan")
            if isinstance(raw_plan, list) and raw_plan:
                existing = session.current_plan
                if existing is None or existing.source_run_id != run_id:
                    try:
                        session = self.store.record_plan(session_id, run_id=run_id, steps=raw_plan)
                    except ValueError:
                        pass

        if status not in STABLE_RUN_STATUSES:
            return
        if session.status != status:
            session = self.store.update_run(session_id, run_id=run_id, status=status, expected_run_id=run_id)
            if session.active_run_id != run_id:
                return
        classification = str(safe.get("classification") or "")
        if any(
            message.role == "assistant"
            and message.run_id == run_id
            and message.metadata.get("status") == status
            and message.metadata.get("classification", "") == classification
            for message in session.messages
        ):
            return
        summary = safe.get("summary")
        changed_files: list[object] = []
        if isinstance(summary, Mapping):
            raw_changed = summary.get("changed_files")
            if isinstance(raw_changed, list):
                changed_files = raw_changed
        message = safe.get("assistant_message")
        if not isinstance(message, str) or not message.strip():
            display = status.replace("_", " ") or "completed"
            message = (
                f"Run {display}. Changed {len(changed_files)} file"
                f"{'s' if len(changed_files) != 1 else ''}."
                if changed_files else f"Run {display}."
            )
        self.store.append_message(
            session_id,
            role="assistant",
            content=message,
            run_id=run_id,
            metadata={"status": status, "classification": classification},
        )
