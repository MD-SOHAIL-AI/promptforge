"""Centralized cross-platform filesystem paths for PromptForge."""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["PathManager"]


class PathManager:
    """Resolve and create PromptForge's code and runtime data directories."""

    __slots__ = ("_root", "_data_root")

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        data_root: str | Path | None = None,
    ) -> None:
        candidate: str | Path
        if root is None:
            candidate = os.getenv("PROMPTFORGE_ROOT") or Path(__file__).parents[2]
        else:
            candidate = root
        if isinstance(candidate, str) and not candidate.strip():
            raise ValueError("root must be a non-empty path")
        if not isinstance(candidate, (str, Path)):
            raise TypeError("root must be a string, Path, or None")
        self._root = Path(candidate).expanduser().resolve()
        self._ensure_directory(self._root)
        data_candidate: str | Path
        if data_root is None:
            data_candidate = (
                os.getenv("PROMPTFORGE_DATA_ROOT")
                or os.getenv("PROMPTFORGE_RUNTIME_ROOT")
                or self._root
            )
        else:
            data_candidate = data_root
        if isinstance(data_candidate, str) and not data_candidate.strip():
            raise ValueError("data_root must be a non-empty path")
        if not isinstance(data_candidate, (str, Path)):
            raise TypeError("data_root must be a string, Path, or None")
        self._data_root = Path(data_candidate).expanduser().resolve()
        self._ensure_directory(self._data_root)

    def project_root(self) -> Path:
        """Return the absolute PromptForge project root."""

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
        """Return the runtime workspace root."""

        return self._ensure_directory(self._data_root / "workspace")

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
        return self._ensure_directory(self._data_root / ".promptforge" / name)

    def _workspace_directory(self, name: str) -> Path:
        return self._ensure_directory(self.workspace_directory() / name)

    @staticmethod
    def _ensure_directory(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise NotADirectoryError(f"managed path is not a directory: {path}")
        return path
