"""Public activity projection of execution events. Never accepts model reasoning fields."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, TypedDict
from .public_progress import public_text


class ActivityRecord(TypedDict):
    agentId: str
    parentId: str | None
    correlation: str
    id: str
    runId: str
    kind: str
    state: str
    eventType: str
    displayStatus: Literal["queued", "running", "success", "warning", "failed"]
    status: Literal["queued", "running", "completed", "failed", "cancelled", "awaiting_approval"]
    title: str
    startedAt: int
    durationMs: int | None
    details: dict[str, object]


def project_activity(
    event: dict, previous: dict | None = None
) -> ActivityRecord | None:
    kind = str(event.get("event_type", "")).lower().replace("_", ".")
    if kind == "tool.pending":
        return None
    tool = event.get("tool", "")
    if kind.startswith("agent.turn.") or (
        kind.startswith("tool.") and tool == "update_plan"
    ):
        return None
    family = None
    if kind.startswith("tool."):
        family = {
            "read_file": "file.read",
            "write_file": "file.edit",
            "edit_file_simple": "file.edit",
            "apply_patch": "file.edit",
            "run_command": "command",
            "build_firmware": "build",
            "update_plan": "plan",
            "grep_search": "search",
            "list_files": "search",
            "glob_files": "search",
            "memory_search": "context",
            "session_event_read": "context",
            "session_event_search": "context",
            "session_trace": "context",
            "present_files": "deliverable",
            "spawn_subagent": "subagent",
            "run_simulation": "simulation",
        }.get(tool, "tool")
    elif kind.startswith(("thinking.", "assistant.summary", "reasoning.summary")):
        family = "thinking"
    elif kind.startswith("context.") or kind == "agent.context.prepared":
        family = "context"
    elif kind.startswith("subagent."):
        family = "subagent"
    elif kind.startswith("plan."):
        family = "plan"
    elif kind.startswith("runtime."):
        family = "approval" if kind == "runtime.awaiting.flash.confirmation" else "run"
    elif kind == "flash.confirmed":
        family = "approval"
    elif kind.startswith("agent.turn."):
        family = "thinking"
    else:
        for prefix in (
            "build.repair",
            "project.repair",
            "project.validation",
            "generation",
            "build",
            "test",
            "simulation",
            "flash",
            "monitor",
            "diagnostic",
            "plan",
            "node",
        ):
            if kind.startswith(prefix + ".") or kind == prefix:
                family = prefix
                break
    if family is None:
        return None
    now = int(
        datetime.fromisoformat(
            str(event["created_at"]).replace("Z", "+00:00")
        ).timestamp()
        * 1000
    )
    identity = (
        str(event.get("agent_id") or "main")
        + ":"
        + family
        + ":"
        + str(
            (event.get("turn") if family == "thinking" else None)
            or event.get("tool_call_id")
            or (event.get("node_id") if family == "node" else "")
            or family
        )
    )
    run_id = str(event.get("run_id", ""))
    old = previous or {}
    status = "queued" if event.get("status") == "queued" else "running"
    if kind.endswith(("failed", "blocked", "timed.out")) or event.get("status") in (
        "failed",
        "blocked",
        "timed_out",
        "interrupted",
    ):
        status = "failed"
    elif kind.endswith("cancelled"):
        status = "cancelled"
    elif event.get("status") in ("completed", "success") or kind.endswith(
        ("completed", "settled", "committed", "confirmed", "generated", "prepared", "created", "stopped")
    ):
        status = "completed"
    if family == "approval" and kind != "flash.confirmed":
        status = "awaiting_approval"
    if family == "thinking" and kind.endswith("summary"):
        status = "completed"
    result = event.get("result") if isinstance(event.get("result"), dict) else {}
    if result.get("success") is False or result.get("exit_code", 0) not in (0, None):
        status = "failed"
    # An explicit start after a terminal action is a new attempt. Outputs and ends
    # reuse the persisted ID, including after process recovery.
    new_attempt = (
        not event.get("tool_call_id")
        and kind.endswith("started")
        and old.get("status")
        in (
            "completed",
            "failed",
            "cancelled",
        )
    )
    activity_id = (
        old.get("id")
        if old and not new_attempt
        else f'{run_id}:{identity}:{event["sequence"]}'
    )
    started = old.get("startedAt", now) if not new_attempt else now
    details = dict(old.get("details", {})) if not new_attempt else {}
    # Explicit allowlist: no arbitrary provider payload, reasoning, or read contents.
    for source in (event.get("arguments", {}), result, event):
        if not isinstance(source, dict):
            continue
        for key in (
            "summary",
            "role",
            "assignment",
            "plan_id",
            "revision",
            "plan_step_id",
            "changed_files",
            "build_passed",
            "path",
            "command",
            "environment",
            "board",
            "port",
            "exit_code",
            "bytes",
            "created",
            "modified",
            "added",
            "removed",
            "diff",
            "errors",
            "warnings",
            "line",
            "column",
            "severity",
            "diagnostics",
            "steps",
            "output",
            "context_files",
            "memory_count",
            "skill_count",
            "recalled_context",
            "turn",
            "step",
            "attempt_id",
            "tool_call_id",
            "tool",
            "artifact_id",
            "build_id",
        ):
            value = source.get(key)
            if key in ("summary", "assignment") and isinstance(value, str):
                value = public_text(value, 1200)
            if isinstance(value, (str, int, float, bool, list)):
                details[key] = (
                    value[:16000]
                    if isinstance(value, str)
                    else value[:100] if isinstance(value, list) else value
                )
    if kind.endswith("output") and isinstance(event.get("output"), str):
        details["output"] = (
            str(old.get("details", {}).get("output", "")) + event["output"]
        )[-16000:]
    if family == "thinking" and kind.startswith(
        ("thinking.summary", "assistant.summary", "reasoning.summary")
    ):
        details["summary"] = public_text(str(event.get("message", "")), 1200)
    if family == "subagent":
        details["summary"] = public_text(str(event.get("message", "")), 1200)
    if family == "context" and not details.get("summary"):
        details["summary"] = public_text(
            str(event.get("message") or "Prepared project context and available tools."),
            1200,
        )
    # Host-generated operational messages are safe public activity labels. They
    # are not model chain-of-thought and make the journal explain what ForgeX
    # actually did without inventing narrative progress.
    if family not in {"thinking", "run"} and not details.get("summary") and event.get("message"):
        details["summary"] = public_text(str(event.get("message")), 1200)
    if tool == "present_files" and isinstance(result.get("files"), list):
        safe_files = []
        for item in result["files"][:32]:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not isinstance(path, str) or not path:
                continue
            safe_files.append({
                "path": path[:1024],
                "description": public_text(str(item.get("description") or ""), 300),
                "bytes": int(item.get("bytes") or 0) if isinstance(item.get("bytes"), int) else 0,
                "sha256": str(item.get("sha256") or "")[:64],
                "staged": item.get("staged") is True,
            })
        details["files"] = safe_files
        details["count"] = len(safe_files)
    for key in ("turn", "step", "attempt_id", "tool_call_id", "tool"):
        value = event.get(key)
        if isinstance(value, (str, int)) and value not in ("", None):
            details[key] = value
    if family == "plan" and details.get("steps"):
        if all(
            isinstance(step, dict)
            and step.get("status") in ("completed", "skipped", "cancelled", "failed")
            for step in details["steps"]
        ):
            status = (
                "failed"
                if any(step.get("status") == "failed" for step in details["steps"])
                else "completed"
            )
    terminal_previous = old.get("status") in ("completed", "failed", "cancelled")
    if (
        terminal_previous
        and not new_attempt
        and family != "plan"
        and status == "running"
    ):
        status = old["status"]
    titles = {
        "thinking": "Thinking / planning",
        "context": "Reading project context",
        "subagent": "Specialist",
        "run": "Task",
        "approval": "Flash approval",
        "file.read": "Read file",
        "file.edit": "Edited staged file",
        "command": "Command",
        "build": "Build",
        "build.repair": "Repairing build errors",
        "project.repair": "Repairing project",
        "project.validation": "Validating project",
        "search": "Inspected project files",
        "plan": "Updated plan",
        "deliverable": "Prepared deliverables",
        "simulation": "Simulation",
    }
    return {
        "agentId": str(event.get("agent_id") or "main"),
        "parentId": (
            str(event["parent_agent_id"]) if event.get("parent_agent_id") else None
        ),
        "id": str(activity_id),
        "runId": run_id,
        "kind": family,
        "state": _state_for(family, status),
        "eventType": _canonical_event_type(kind, family, status, details),
        "displayStatus": _display_status(status, details),
        "status": status,
        "title": titles.get(family, family.replace(".", " ").capitalize()),
        "startedAt": int(started),
        "durationMs": (
            None
            if status in ("running", "awaiting_approval")
            else int(
                event.get("duration_ms")
                or (
                    old.get("durationMs")
                    if terminal_previous and not new_attempt
                    else None
                )
                or max(0, now - int(started))
            )
        ),
        "details": details,
        "correlation": identity,
    }


def _state_for(family: str, status: str) -> str:
    if status == "failed":
        return "error"
    if family == "run" and status == "completed":
        return "completed"
    if family == "run" and status == "queued":
        return "idle"
    if family == "approval" and status == "awaiting_approval":
        return "waiting_for_approval"
    return {
        "thinking": "thinking",
        "context": "recalling_context",
        "plan": "planning",
        "file.read": "reading",
        "file.edit": "editing",
        "search": "searching",
        "command": "running_command",
        "build": "building",
        "test": "testing",
        "simulation": "testing",
        "build.repair": "repairing",
        "project.repair": "repairing",
        "deliverable": "tool_call",
    }.get(family, "tool_call")


def _display_status(status: str, details: dict[str, object]) -> str:
    if status == "failed":
        return "failed"
    if status == "queued":
        return "queued"
    if status in {"running", "awaiting_approval"}:
        return "running"
    warnings = details.get("warnings")
    if status in {"cancelled", "interrupted"} or (
        isinstance(warnings, (int, float)) and warnings > 0
    ):
        return "warning"
    return "success"


def _canonical_event_type(
    kind: str, family: str, status: str, details: dict[str, object]
) -> str:
    """Expose stable public lifecycle names while accepting legacy emitters."""
    if family == "thinking":
        return "thinking.summary" if status == "completed" else "thinking.started"
    if family == "context":
        return "context.completed" if status == "completed" else "context.started"
    if family == "plan":
        return "plan.created" if status == "completed" else "plan.step.started"
    if family == "run":
        return {
            "queued": "agent.started",
            "running": "agent.started",
            "completed": "agent.completed",
            "failed": "agent.error",
            "cancelled": "agent.completed",
        }.get(status, "agent.started")
    if family == "file.edit":
        return "file.created" if details.get("created") is True else "file.modified"
    suffix = {
        "queued": "queued",
        "running": "started",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "awaiting_approval": "required",
    }.get(status, "updated")
    return f"{'approval' if family == 'approval' else family}.{suffix}"
