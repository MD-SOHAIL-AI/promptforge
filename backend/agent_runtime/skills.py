"""Project-local Forge Agent skill discovery and lazy loading."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
            if not root.is_dir() or root.is_symlink():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                if skill_file.is_symlink() or not skill_file.is_file():
                    continue
                name = skill_file.parent.name
                if not _safe_name(name):
                    continue
                try:
                    text = skill_file.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
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
            try:
                resolved = skill_file.resolve(strict=True)
                resolved.relative_to(root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if resolved.is_symlink() or not resolved.is_file():
                continue
            text = resolved.read_text(encoding="utf-8")
            return text[:max_chars]
        raise KeyError("AGENT_SKILL_NOT_FOUND")


def _safe_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and all(ch.isalnum() or ch in "_-" for ch in name)
