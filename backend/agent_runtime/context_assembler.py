"""Lean, tool-first context assembly for Forge Agent V3."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from backend.changes import WorkspaceSnapshot


@dataclass(frozen=True, slots=True)
class AgentContext:
    task: str
    project_instructions: tuple[str, ...]
    workspace_summary: str
    memories: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()

    def render(self) -> str:
        sections = [
            "USER TASK:\n" + self.task.strip(),
            "WORKSPACE SUMMARY:\n" + self.workspace_summary,
        ]
        if self.project_instructions:
            sections.append("PROJECT INSTRUCTIONS:\n" + "\n\n".join(self.project_instructions))
        if self.memories:
            sections.append("VERIFIED PROJECT MEMORY:\n" + "\n".join(f"- {item}" for item in self.memories))
        if self.skills:
            sections.append("AVAILABLE SKILLS:\n" + "\n".join(f"- {item}" for item in self.skills))
        sections.append(
            "Use list/glob/grep/read tools to acquire only the context you need. "
            "Do not assume file contents from names. Keep edits minimal, build when verification is requested, "
            "and continue using tool observations until the task is actually complete."
        )
        return "\n\n".join(sections)


class ContextAssembler:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)

    def assemble(
        self,
        *,
        task: str,
        snapshot: WorkspaceSnapshot,
        memories: Iterable[Mapping[str, object]] = (),
        skills: Iterable[str] = (),
        max_inventory: int = 120,
    ) -> AgentContext:
        instructions: list[str] = []
        for name in ("FORGEX.md", "AGENTS.md"):
            path = self.workspace_root / name
            if path.is_file() and not path.is_symlink():
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                instructions.append(f"[{name}]\n{text[:16_000]}")
        paths = sorted(snapshot.files)
        shown = paths[:max_inventory]
        suffix = "" if len(paths) <= max_inventory else f"\n... +{len(paths)-max_inventory} more files (use list/glob/grep)."
        summary = (
            f"Revision: {snapshot.revision}\n"
            f"Files: {len(paths)}\n"
            + "\n".join(shown)
            + suffix
        )
        verified = []
        for memory in memories:
            if memory.get("status") != "active" or memory.get("verified") is not True:
                continue
            kind = str(memory.get("kind") or "memory")
            value = str(memory.get("value") or "").strip()
            if value:
                verified.append(f"{kind}: {value[:700]}")
        return AgentContext(
            task=task,
            project_instructions=tuple(instructions),
            workspace_summary=summary,
            memories=tuple(verified[:12]),
            skills=tuple(skills),
        )
