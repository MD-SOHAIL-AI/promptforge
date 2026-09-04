"""Safe high-level activity events for Forge agent sessions and runs."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping


class AgentActivity(str, Enum):
    THINKING = "thinking"
    SLEUTHING = "sleuthing"
    INSPECTING = "inspecting"
    READING = "reading"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    SEARCHING = "searching"
    PREPARING = "preparing"
    EDITING = "editing"
    GENERATING = "generating"
    BUILDING = "building"
    DIAGNOSING = "diagnosing"
    REPAIRING = "repairing"
    CHECKING = "checking"
    TESTING = "testing"
    CONNECTING = "connecting"
    FLASHING = "flashing"
    MONITORING = "monitoring"
    WAITING = "waiting"
    FINISHING = "finishing"
    EXPLAINING = "explaining"


ACTIVITY_LABELS: Mapping[AgentActivity, str] = {
    AgentActivity.THINKING: "Calibrating",
    AgentActivity.SLEUTHING: "Tracing",
    AgentActivity.INSPECTING: "Inspecting",
    AgentActivity.READING: "Reading",
    AgentActivity.ANALYZING: "Analyzing",
    AgentActivity.PLANNING: "Planning",
    AgentActivity.SEARCHING: "Searching",
    AgentActivity.PREPARING: "Priming",
    AgentActivity.EDITING: "Shaping",
    AgentActivity.GENERATING: "Forging",
    AgentActivity.BUILDING: "Building",
    AgentActivity.DIAGNOSING: "Diagnosing",
    AgentActivity.REPAIRING: "Repairing",
    AgentActivity.CHECKING: "Verifying",
    AgentActivity.TESTING: "Testing",
    AgentActivity.CONNECTING: "Linking",
    AgentActivity.FLASHING: "Flashing",
    AgentActivity.MONITORING: "Listening",
    AgentActivity.WAITING: "Awaiting confirmation",
    AgentActivity.FINISHING: "Wrapping up",
    AgentActivity.EXPLAINING: "Explaining",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class AgentActivityEvent:
    sequence: int
    event_type: str
    activity: AgentActivity
    session_id: str
    phase: str = "active"
    run_id: str | None = None
    message_id: str | None = None
    created_at: str = ""

    def to_safe_dict(self) -> dict[str, object]:
        created = self.created_at or utc_now()
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "type": self.event_type,
            "activity": self.activity.value,
            "label": ACTIVITY_LABELS[self.activity],
            "phase": self.phase,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "message_id": self.message_id,
            "created_at": created,
            "timestamp": created,
        }


class AgentActivityBroker:
    """Small in-memory activity feed keyed by agent session."""

    def __init__(
        self,
        *,
        max_events_per_session: int = 200,
        event_sink: Callable[[str, Mapping[str, object]], object] | None = None,
    ) -> None:
        self._max_events = max(10, int(max_events_per_session))
        self._events: dict[str, list[dict[str, object]]] = {}
        self._lock = threading.RLock()
        self._event_sink = event_sink

    def emit(
        self,
        session_id: str,
        activity: AgentActivity,
        *,
        event_type: str = "activity.updated",
        phase: str = "active",
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            values = self._events.setdefault(session_id, [])
            event = AgentActivityEvent(
                sequence=(int(values[-1]["sequence"]) + 1) if values else 1,
                event_type=event_type,
                activity=activity,
                phase=phase,
                session_id=session_id,
                run_id=run_id,
                message_id=message_id,
                created_at=utc_now(),
            ).to_safe_dict()
            values.append(event)
            if len(values) > self._max_events:
                del values[: len(values) - self._max_events]
            result = dict(event)
        if self._event_sink is not None:
            try:
                self._event_sink(session_id, result)
            except Exception:
                # Durable activity is additive; presentation must remain available.
                pass
        return result

    def complete(self, session_id: str, *, run_id: str | None = None, message_id: str | None = None) -> dict[str, object]:
        return self.emit(
            session_id,
            AgentActivity.FINISHING,
            event_type="activity.completed",
            phase="completed",
            run_id=run_id,
            message_id=message_id,
        )

    def recent(self, session_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        with self._lock:
            values = tuple(dict(item) for item in self._events.get(session_id, ()) if int(item["sequence"]) > after_sequence)
        return values

    def last_sequence(self, session_id: str) -> int:
        with self._lock:
            values = self._events.get(session_id, [])
            return int(values[-1]["sequence"]) if values else 0


def run_activity_fields(event: Mapping[str, object]) -> dict[str, object]:
    activity = activity_for_run_event(event)
    if activity is None:
        return {}
    phase = "completed" if str(event.get("status") or "") in {"completed", "failed", "cancelled", "blocked", "timed_out"} else "active"
    return {
        "activity": activity.value,
        "activity_label": ACTIVITY_LABELS[activity],
        "activity_phase": phase,
    }


def activity_for_run_event(event: Mapping[str, object]) -> AgentActivity | None:
    event_type = str(event.get("event_type") or "").casefold()
    stage = str(event.get("stage") or "").casefold()
    status = str(event.get("status") or "").casefold()
    tool = str(event.get("tool") or "").casefold()

    if event_type == "runtime.queued":
        return AgentActivity.PLANNING if stage == "planning" else AgentActivity.PREPARING
    if event_type == "runtime.started":
        return AgentActivity.PLANNING if stage == "planning" else AgentActivity.PREPARING
    if event_type == "runtime.cancelling":
        return AgentActivity.FINISHING
    if event_type == "runtime.cancelled":
        return AgentActivity.FINISHING
    if event_type == "runtime.awaiting_flash_confirmation":
        return AgentActivity.WAITING
    if event_type == "runtime.completed":
        return AgentActivity.FINISHING
    if event_type == "runtime.failed":
        return AgentActivity.DIAGNOSING if stage == "build" else AgentActivity.FINISHING
    if event_type == "runtime.blocked":
        return AgentActivity.WAITING
    if event_type == "flash.confirmed":
        return AgentActivity.CONNECTING
    if event_type == "changes.stage.created":
        return AgentActivity.EDITING
    if event_type in {"planner.turn.started", "agent.turn.started", "agent_turn_started"}:
        return AgentActivity.THINKING
    if event_type in {"planner.turn.completed", "agent.turn.completed", "agent_turn_completed"}:
        return AgentActivity.CHECKING
    if event_type in {"tool.started", "tool_started", "tool.completed", "tool_completed"}:
        if tool in {"list_files", "glob_files"}:
            return AgentActivity.INSPECTING
        if tool in {"grep_search", "memory_search"}:
            return AgentActivity.SEARCHING
        if tool in {"read_file", "load_skill"}:
            return AgentActivity.READING
        if tool in {"write_file", "edit_file_simple"}:
            return AgentActivity.EDITING
        if tool in {"update_plan"}:
            return AgentActivity.PLANNING
        if tool in {"build_firmware", "run_command"}:
            return AgentActivity.BUILDING if tool == "build_firmware" else AgentActivity.TESTING
        return AgentActivity.CHECKING
    if event_type == "plan_generated":
        return AgentActivity.PLANNING
    if event_type.startswith("generation_"):
        return AgentActivity.GENERATING
    if event_type == "project_validation_started":
        return AgentActivity.CHECKING
    if event_type == "project_validation_failed":
        return AgentActivity.DIAGNOSING
    if event_type == "project_repair_started":
        return AgentActivity.REPAIRING
    if event_type == "project_repair_completed":
        return AgentActivity.CHECKING
    if event_type == "project_repair_failed":
        return AgentActivity.DIAGNOSING
    if event_type == "project_validation_completed":
        return AgentActivity.CHECKING
    if event_type == "build_started":
        return AgentActivity.BUILDING
    if event_type == "build_completed":
        return AgentActivity.CHECKING
    if event_type == "build_failed":
        return AgentActivity.DIAGNOSING
    if event_type == "build_repair_started":
        return AgentActivity.REPAIRING
    if event_type == "build_repair_completed":
        return AgentActivity.CHECKING
    if event_type == "build_repair_failed":
        return AgentActivity.DIAGNOSING
    if event_type.startswith("flash_build"):
        return AgentActivity.CHECKING
    if event_type.startswith("flash"):
        return AgentActivity.FLASHING
    if event_type.startswith("monitor"):
        return AgentActivity.MONITORING
    if status == "running" and stage == "build":
        return AgentActivity.BUILDING
    if status == "running" and stage == "repair":
        return AgentActivity.REPAIRING
    return None
