"""Project durable Agent run state into persistent user-facing sessions."""
from __future__ import annotations

from typing import Any, Mapping

from .session_store import AgentSessionStore

STABLE_RUN_STATUSES = frozenset({
    "completed", "failed", "cancelled", "blocked", "timed_out",
    "awaiting_flash_confirmation", "paused_model_unavailable",
})


class AgentSessionRunProjector:
    """Synchronous idempotent projection invoked after orchestrator persistence."""

    def __init__(self, store: AgentSessionStore) -> None:
        self.store = store

    def project(self, run: Any) -> None:
        safe = run.to_safe_dict()
        run_id = str(safe.get("run_id") or "")
        if not run_id:
            return
        sessions = tuple(self.store.list())
        for listed in sessions:
            if listed.active_run_id != run_id:
                continue
            self._project_session(listed.session_id, run, safe)

    def _project_session(self, session_id: str, run: Any, safe: Mapping[str, object]) -> None:
        status = str(safe.get("status") or "")
        run_id = str(safe.get("run_id") or "")
        try:
            session = self.store.get(session_id)
        except KeyError:
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
        session = self.store.update_run(session_id, run_id=run_id, status=status)
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
