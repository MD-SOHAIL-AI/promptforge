"""Deterministic user-facing slash commands for Agent sessions."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSlashCommand:
    name: str
    instruction: str
    autonomy: str | None = None


_ROLE_COMMANDS = frozenset({"explore", "review", "verify"})


def parse_agent_slash_command(content: str) -> AgentSlashCommand | None:
    text = content.strip()
    if not text.startswith("/"):
        return None
    match = re.fullmatch(r"/([a-z][a-z0-9_-]*)(?:\s+([\s\S]*?))?\s*", text, re.IGNORECASE)
    if not match:
        raise ValueError("AGENT_SLASH_COMMAND_INVALID")
    name = match.group(1).casefold()
    arguments = (match.group(2) or "").strip()
    if name in {"agent", "execute"}:
        return AgentSlashCommand(name, _required_task(name, arguments))
    if name == "plan":
        task = _required_task(name, arguments)
        return AgentSlashCommand(
            name,
            f"Create a read-only implementation plan for this request: {task}",
            autonomy="plan_only",
        )
    if name in _ROLE_COMMANDS:
        task = arguments or (
            "Review the current project for correctness, hardware risks, and missing validation."
            if name == "review"
            else "Verify the current project with read-only inspection and a trusted build."
            if name == "verify"
            else ""
        )
        task = _required_task(name, task)
        return _subagent_command(name, task)
    if name == "subagent":
        role, separator, task = arguments.partition(" ")
        role = role.casefold().strip()
        if role not in _ROLE_COMMANDS or not separator or not task.strip():
            raise ValueError("AGENT_SLASH_SUBAGENT_USAGE")
        return _subagent_command(role, task.strip(), name="subagent")
    if name == "build":
        detail = arguments or "the current firmware project"
        return AgentSlashCommand(name, f"Build {detail}. Report trusted compiler diagnostics and do not flash hardware.")
    if name == "repair":
        detail = arguments or "the latest build failure"
        return AgentSlashCommand(name, f"Diagnose and repair {detail}, then rebuild to verify the result. Do not flash hardware.")
    if name == "simulate":
        detail = arguments or "the current firmware behavior"
        return AgentSlashCommand(name, f"Build and simulate {detail}. Report observed simulation evidence and do not flash hardware.")
    if name == "flash":
        if arguments:
            raise ValueError("AGENT_SLASH_COMMAND_TAKES_NO_ARGUMENTS")
        return AgentSlashCommand(name, "Prepare the latest verified firmware for flashing. Require explicit one-time confirmation before upload.")
    raise ValueError("AGENT_SLASH_COMMAND_UNKNOWN")


def _required_task(name: str, value: str) -> str:
    if not value:
        raise ValueError(f"AGENT_SLASH_{name.upper()}_TASK_REQUIRED")
    return value


def _subagent_command(role: str, task: str, *, name: str | None = None) -> AgentSlashCommand:
    return AgentSlashCommand(
        name or role,
        (
            "Inspect this project without modifying files. You must delegate exactly one bounded "
            f"'{role}' subagent using the spawn_subagent tool for this task: {task} "
            "Return the subagent's evidence and clearly label any uncertainty."
        ),
        autonomy="read_only",
    )
