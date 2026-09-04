"""Centralized cross-platform filesystem paths for ForgeX."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["PathManager"]


class PathManager:
    """Resolve and create ForgeX managed filesystem directories."""

    __slots__ = ("_root",)

    def __init__(self, root: str | Path | None = None) -> None:
        candidate: str | Path
        if root is None:
            candidate = os.getenv("FORGEX_ROOT") or os.getenv("PROMPTFORGE_ROOT") or Path(__file__).parents[2]
        else:
            candidate = root
        if isinstance(candidate, str) and not candidate.strip():
            raise ValueError("root must be a non-empty path")
        if not isinstance(candidate, (str, Path)):
            raise TypeError("root must be a string, Path, or None")
        self._root = Path(candidate).expanduser().resolve()
        self._ensure_directory(self._root)

    def project_root(self) -> Path:
        """Return the absolute ForgeX project root."""

        return self._ensure_directory(self._root)

    def projects_directory(self) -> Path:
        """Return the managed generated-project directory."""

        return self._managed_directory("projects")

    def artifacts_directory(self) -> Path:
        """Return the managed build and execution artifact directory."""

        return self._managed_directory("artifacts")

    def logs_directory(self) -> Path:
        """Return the managed structured-log directory."""

        return self._managed_directory("logs")

    def state_directory(self) -> Path:
        """Return the managed persistent runtime-state directory."""

        return self._managed_directory("state")

    def workspace_directory(self) -> Path:
        """Return the repository workspace root."""

        return self._ensure_directory(self._root / "workspace")

    def workspace_projects_directory(self) -> Path:
        """Return the workspace project directory."""

        return self._workspace_directory("projects")

    def workspace_builds_directory(self) -> Path:
        """Return the workspace build directory."""

        return self._workspace_directory("builds")

    def workspace_artifacts_directory(self) -> Path:
        """Return the workspace artifact directory."""

        return self._workspace_directory("artifacts")

    def workspace_logs_directory(self) -> Path:
        """Return the workspace log directory."""

        return self._workspace_directory("logs")

    def _managed_directory(self, name: str) -> Path:
        return self._ensure_directory(self._root / ".promptforge" / name)

    def _workspace_directory(self, name: str) -> Path:
        return self._ensure_directory(self.workspace_directory() / name)

    @staticmethod
    def _ensure_directory(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise NotADirectoryError(f"managed path is not a directory: {path}")
        return path
