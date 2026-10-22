"""Deterministic derived projections for ForgeX journal + active transient attempt."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

PROJECTION_REDUCER_VERSION = "forgex.projection.v1"


@dataclass(slots=True)
class SessionProjection:
    session_id: str
    session_seq: int = 0
    run_status: str = "idle"
    active_turn: int | None = None
    active_step: int | None = None
    active_attempt_id: str | None = None
    assistant_content: str = ""
    activities: list[dict[str, object]] = field(default_factory=list)
    plan: list[dict[str, object]] = field(default_factory=list)
    pending_tools: dict[str, dict[str, object]] = field(default_factory=dict)
    approvals: dict[str, dict[str, object]] = field(default_factory=dict)
    queued_guidance: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value):
        from dataclasses import fields
        return cls(**{item.name: value[item.name] for item in fields(cls) if item.name in value})

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "session_seq": self.session_seq,
            "run_status": self.run_status,
            "active_turn": self.active_turn,
            "active_step": self.active_step,
            "active_attempt_id": self.active_attempt_id,
            "assistant_content": self.assistant_content,
            "activities": [dict(item) for item in self.activities[-500:]],
            "plan": [dict(item) for item in self.plan],
            "pending_tools": {key: dict(value) for key, value in self.pending_tools.items()},
            "approvals": {key: dict(value) for key, value in self.approvals.items()},
            "queued_guidance": [dict(item) for item in self.queued_guidance],
        }


class SessionProjectionEngine:
    def rebuild(self, session_id: str, events: Sequence[Mapping[str, object]]) -> SessionProjection:
        state = SessionProjection(session_id=session_id)
        for event in events:
            self.apply_event(state, event)
        return state

    def apply_event(self, state: SessionProjection, event: Mapping[str, object]) -> SessionProjection:
        seq = int(event.get("session_seq") or event.get("sequence") or state.session_seq)
        if seq and seq <= state.session_seq:
            return state
        if seq:
            state.session_seq = seq
        event_type = str(event.get("event_type") or "")
        if event_type in {"TURN_STARTED", "turn.started"}:
            state.active_turn = _int(event.get("turn")); state.run_status = "running"
        elif event_type in {"STEP_STARTED", "step.started"}:
            state.active_step = _int(event.get("step"))
        elif event_type in {"MODEL_ATTEMPT_STARTED", "model.attempt.started"}:
            state.active_attempt_id = _str(event.get("attempt_id")); state.assistant_content = ""
        elif event_type in {"MODEL_ATTEMPT_SETTLED", "model.attempt.settled"}:
            state.active_attempt_id = None
            content = event.get("content") or event.get("message")
            if isinstance(content, str): state.assistant_content = content
        elif event_type in {"runtime.completed", "runtime.completed_with_warning", "runtime.failed", "runtime.cancelled", "runtime.blocked", "runtime.interrupted", "runtime.paused_model_unavailable"}:
            state.run_status = str(event.get("status") or ("failed" if event_type == "runtime.interrupted" else event_type.removeprefix("runtime.")))
            state.active_turn = None; state.active_step = None; state.active_attempt_id = None
            state.pending_tools.clear()
            for activity in state.activities:
                if activity.get("state") in {"active", "running", "pending"} or activity.get("status") in {"running", "pending"}:
                    activity.update({"state": "completed" if state.run_status.startswith("completed") else "error",
                                     "status": state.run_status, "displayStatus": state.run_status})
        elif event_type in {"tool.invocation.admitted", "TOOL_PENDING", "tool.execution.started", "TOOL_STARTED"}:
            call_id = _str(event.get("call_id") or event.get("tool_call_id"))
            if call_id: state.pending_tools[call_id] = dict(event)
        elif event_type in {"tool.result.committed", "TOOL_COMPLETED", "TOOL_FAILED", "tool.execution.settled"}:
            call_id = _str(event.get("call_id") or event.get("tool_call_id"))
            if call_id: state.pending_tools.pop(call_id, None)
        elif event_type in {"PLAN_UPDATED", "plan.revised", "plan.created"}:
            steps = event.get("steps")
            if isinstance(steps, list): state.plan = [dict(item) for item in steps if isinstance(item, Mapping)]
        elif event_type == "agent.inbox.inserted":
            state.queued_guidance.append({"item_id": event.get("queue_item_id"), "message": event.get("message"), "mode": event.get("mode")})
        elif event_type in {"agent.inbox.removed", "agent.inbox.claimed"}:
            item_id = event.get("queue_item_id"); state.queued_guidance = [item for item in state.queued_guidance if item.get("item_id") != item_id]
        elif event_type.startswith("approval."):
            approval_id = _str(event.get("approval_id"))
            if approval_id: state.approvals[approval_id] = dict(event)
        activity = event.get("activity_record")
        if isinstance(activity, Mapping):
            state.activities.append(dict(activity))
        return state

    def apply_transient(self, state: SessionProjection, frame: Mapping[str, object]) -> SessionProjection:
        if state.run_status in {"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out", "paused_model_unavailable"}:
            return state
        if str(frame.get("event_type") or "") in {"assistant.content.delta", "ASSISTANT_CONTENT_DELTA"}:
            attempt = _str(frame.get("attempt_id"))
            if attempt and state.active_attempt_id not in {None, attempt}:
                return state
            state.active_attempt_id = attempt or state.active_attempt_id
            delta = frame.get("delta") or frame.get("message")
            if isinstance(delta, str): state.assistant_content += delta
        return state


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None

def _int(value: object) -> int | None:
    try: return int(value) if value is not None else None
    except (TypeError, ValueError): return None
