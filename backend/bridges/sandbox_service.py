"""Safe workspace sandbox copy support for bridge dry-runs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .diff_service import IGNORED_DIRS, is_safe_relative_path


class BridgeSandboxError(ValueError):
    code = "BRIDGE_SANDBOX_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class BridgeSandboxService:
    def __init__(self, sandbox_root: str | Path) -> None:
        self.sandbox_root = Path(sandbox_root)

    def create_sandbox(self, *, run_id: str, workspace_root: str | Path) -> Path:
        source = Path(workspace_root).expanduser().resolve()
        if not source.exists() or not source.is_dir():
            raise BridgeSandboxError("Workspace root must be an existing directory.", {"workspace_root": str(source)})
        destination = (self.sandbox_root / run_id).resolve()
        base = self.sandbox_root.resolve()
        try:
            destination.relative_to(base)
        except ValueError as exc:
            raise BridgeSandboxError("Sandbox destination escaped managed sandbox root.", {"run_id": run_id}) from exc
        if destination.exists():
            raise BridgeSandboxError("Sandbox already exists for run.", {"run_id": run_id})
        destination.mkdir(parents=True, exist_ok=False)
        self._copy_workspace(source, destination)
        return destination

    def cleanup_expired_sandboxes(self, *, keep_latest: int = 20) -> int:
        if not self.sandbox_root.exists():
            return 0
        children = [path for path in self.sandbox_root.iterdir() if path.is_dir()]
        children.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        removed = 0
        for child in children[max(0, keep_latest) :]:
            if self._safe_child(child):
                shutil.rmtree(child)
                removed += 1
        return removed

    def cleanup_sandbox(self, *, run_id: str) -> bool:
        """Remove one owned sandbox after the same containment check used by retention cleanup."""

        child = (self.sandbox_root / run_id).resolve()
        if not child.exists():
            return False
        if not self._safe_child(child):
            raise BridgeSandboxError("Sandbox cleanup target escaped managed sandbox root.", {"run_id": run_id})
        shutil.rmtree(child)
        return True

    def _copy_workspace(self, source: Path, destination: Path) -> None:
        for current_root, dirnames, filenames in os.walk(source, followlinks=False):
            dirnames[:] = [
                name
                for name in dirnames
                if name.casefold() not in IGNORED_DIRS and not (Path(current_root) / name).is_symlink()
            ]
            current = Path(current_root)
            for filename in filenames:
                source_path = current / filename
                if source_path.is_symlink():
                    continue
                resolved = source_path.resolve()
                try:
                    relative = resolved.relative_to(source).as_posix()
                except ValueError as exc:
                    raise BridgeSandboxError("Workspace copy attempted to leave workspace root.", {"path": str(source_path)}) from exc
                if not is_safe_relative_path(relative):
                    raise BridgeSandboxError("Unsafe workspace path rejected.", {"path": relative})
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, target)

    def _safe_child(self, child: Path) -> bool:
        try:
            child.resolve().relative_to(self.sandbox_root.resolve())
        except ValueError:
            return False
        return child.is_dir()
