"""Project-local Forge Agent skill discovery and lazy loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from backend.security.workspace_io import checked_path, read_context_text


def _read_skill(boundary: Path, path: Path, limit: int) -> str:
    try:
        return read_context_text(boundary, path, limit)
    except ValueError as exc:
        raise ValueError("AGENT_SKILL_PATH_OR_CONTENT_UNSAFE") from exc


@dataclass(frozen=True, slots=True)
class SkillInfo:
    name: str
    path: str
    summary: str

    def label(self) -> str:
        return f"{self.name}: {self.summary}"


class SkillRegistry:
    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        builtin = Path(__file__).resolve().parent / "builtin_skills"
        self.roots = (
            self.workspace_root / ".forgex" / "skills",
            self.workspace_root / ".agents" / "skills",
            builtin,
        )

    def discover(self) -> tuple[SkillInfo, ...]:
        found: dict[str, SkillInfo] = {}
        for root in self.roots:
            boundary = self.workspace_root if root != self.roots[-1] else root.parent
            try:
                checked_path(boundary, root)
            except (OSError, ValueError):
                continue
            if not root.is_dir():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                if skill_file.is_symlink() or not skill_file.is_file():
                    continue
                name = skill_file.parent.name
                if not _safe_name(name):
                    continue
                try:
                    text = _read_skill(boundary, skill_file, 96_000)
                except (OSError, ValueError, UnicodeError):
                    continue
                summary = next((line.lstrip("# ").strip() for line in text.splitlines() if line.strip()), name)
                try:
                    display_path = str(skill_file.relative_to(self.workspace_root))
                except ValueError:
                    display_path = f"builtin:{name}"
                found.setdefault(name, SkillInfo(name=name, path=display_path, summary=summary[:180]))
        return tuple(found.values())

    def load(self, name: str, *, max_chars: int = 24_000) -> str:
        if not _safe_name(name):
            raise ValueError("AGENT_SKILL_NAME_INVALID")
        for root in self.roots:
            skill_file = root / name / "SKILL.md"
            boundary = self.workspace_root if root != self.roots[-1] else root.parent
            try:
                text = _read_skill(boundary, skill_file, 96_000)
            except (OSError, ValueError):
                continue
            return text[:max_chars]
        raise KeyError("AGENT_SKILL_NOT_FOUND")


def _safe_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and all(ch.isalnum() or ch in "_-" for ch in name)
