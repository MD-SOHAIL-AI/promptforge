from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zipfile import ZIP_DEFLATED, ZipFile

from backend.observability.runtime_logs import RuntimeLogger
from backend.state.persistence import PersistenceManager
from backend.utils.paths import PathManager

__all__ = ["WorkspaceManager", "WorkspaceError", "WorkspacePathError"]

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorkspaceError(RuntimeError):
    pass


class WorkspacePathError(WorkspaceError, ValueError):
    pass


class WorkspaceManager:
    def __init__(
        self,
        paths: PathManager,
        *,
        clock: Callable[[], datetime] | None = None,
        persistence: PersistenceManager | None = None,
        runtime_logger: RuntimeLogger | None = None,
    ) -> None:
        self.paths = paths
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.persistence = persistence
        self.runtime_logger = runtime_logger
        self.workspace_root = self.paths.workspace_directory()
        self.projects_root = self.paths.workspace_projects_directory()
        self.builds_root = self.paths.workspace_builds_directory()
        self.artifacts_root = self.paths.workspace_artifacts_directory()
        self.logs_root = self.paths.workspace_logs_directory()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_name(value: str, *, label: str = "name") -> str:
        if not isinstance(value, str) or not value or not _NAME_RE.fullmatch(value):
            raise WorkspacePathError(f"invalid {label}: {value!r}")
        if value in {".", ".."}:
            raise WorkspacePathError(f"invalid {label}: {value!r}")
        return value

    @staticmethod
    def _assert_managed(root: Path, candidate: Path) -> Path:
        root_resolved = root.resolve()
        candidate_resolved = candidate.resolve(strict=False)
        if candidate_resolved != root_resolved and root_resolved not in candidate_resolved.parents:
            raise WorkspacePathError(f"path escapes managed workspace: {candidate}")
        return candidate

    def _project_path(self, name: str) -> Path:
        self._validate_name(name, label="project name")
        return self._assert_managed(self.projects_root, self.projects_root / name)

    def create_project(
        self,
        project_name: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        target_board: str | None = None,
        framework: str | None = None,
        platformio_ini: str | None = None,
    ) -> dict[str, Any]:
        path = self._project_path(project_name)
        if path.exists() and path.is_symlink():
            raise WorkspacePathError("project path may not be a symlink")
        path.mkdir(parents=True, exist_ok=True)
        (path / "src").mkdir(exist_ok=True)
        (path / "include").mkdir(exist_ok=True)
        if platformio_ini is not None:
            (path / "platformio.ini").write_text(platformio_ini, encoding="utf-8")
        now = self._iso(self._now())
        metadata: dict[str, Any] = {
            "project_name": project_name,
            "project_id": project_id or project_name,
            "task_id": task_id,
            "execution_id": execution_id,
            "target_board": target_board,
            "framework": framework,
            "project_path": str(path.resolve()),
            "status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
        }
        self._write_json(path / "metadata.json", metadata)
        if self.persistence is not None:
            self.persistence.save_project(metadata, project_id=metadata["project_id"])
        if self.runtime_logger is not None:
            self.runtime_logger.log_workflow(
                "Workspace project created",
                task_id=task_id,
                execution_id=execution_id,
                metadata={"project_name": project_name, "project_id": metadata["project_id"]},
            )
        return metadata

    def project_exists(self, project_name: str) -> bool:
        path = self._project_path(project_name)
        return path.is_dir() and not path.is_symlink()

    def project_metadata(self, project_name: str) -> dict[str, Any]:
        path = self._project_path(project_name) / "metadata.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_projects(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in sorted(self.projects_root.iterdir(), key=lambda p: p.name):
            if not path.is_dir() or path.is_symlink():
                continue
            metadata = path / "metadata.json"
            if metadata.is_file():
                try:
                    result.append(json.loads(metadata.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return result

    def archive_project(self, project_name: str) -> Path:
        path = self._project_path(project_name)
        metadata = self.project_metadata(project_name)
        metadata["status"] = "ARCHIVED"
        metadata["updated_at"] = self._iso(self._now())
        self._write_json(path / "metadata.json", metadata)
        archive_root = self.workspace_root / "archives"
        archive_root.mkdir(parents=True, exist_ok=True)
        archive_path = archive_root / f"{project_name}.zip"
        with ZipFile(archive_path, "w", ZIP_DEFLATED) as zf:
            for item in sorted(path.rglob("*")):
                if item.is_file() and not item.is_symlink():
                    zf.write(item, Path(project_name) / item.relative_to(path))
        return archive_path

    def delete_project(self, project_name: str) -> bool:
        path = self._project_path(project_name)
        if not path.exists():
            return False
        if path.is_symlink():
            raise WorkspacePathError("project path may not be a symlink")
        shutil.rmtree(path)
        return True

    def save_build(self, execution_id: str, files: Any, data: bytes | str | None = None, *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._validate_name(execution_id, label="execution id")
        root = self._assert_managed(self.builds_root, self.builds_root / execution_id)
        root.mkdir(parents=True, exist_ok=True)
        if isinstance(files, Mapping):
            entries = files.items()
        else:
            entries = [(files, data)]
        for name, payload in entries:
            safe_name = self._validate_name(str(name), label="build filename")
            self._write_payload(root / safe_name, payload)
        now = self._iso(self._now())
        metadata_dict = dict(metadata or {})
        metadata_dict.setdefault("created_at", now)
        metadata_dict.setdefault("updated_at", now)
        record: dict[str, Any] = {
            "execution_id": execution_id,
            "path": str(root.resolve()),
            "created_at": now,
            "metadata": metadata_dict,
        }
        self._write_json(root / "metadata.json", record)
        if self.persistence is not None:
            self.persistence.save_build(execution_id, record, execution_id=execution_id)
        if self.runtime_logger is not None:
            self.runtime_logger.log_build("Workspace build saved", execution_id=execution_id, metadata={"path": record["path"]})
        return record

    def get_build(self, execution_id: str, filename: str) -> bytes:
        self._validate_name(execution_id, label="execution id")
        self._validate_name(filename, label="build filename")
        path = self._assert_managed(self.builds_root, self.builds_root / execution_id / filename)
        return path.read_bytes()

    def list_builds(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in self.builds_root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            metadata = path / "metadata.json"
            if metadata.is_file():
                try:
                    result.append(json.loads(metadata.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return sorted(result, key=lambda row: (row.get("created_at", ""), row.get("execution_id", "")))

    def latest_build(self) -> dict[str, Any] | None:
        builds = self.list_builds()
        return builds[-1] if builds else None

    def save_artifact(self, execution_id: str, filename: str, payload: Any) -> Path:
        self._validate_name(execution_id, label="execution id")
        self._validate_name(filename, label="artifact filename")
        root = self._assert_managed(self.artifacts_root, self.artifacts_root / execution_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / filename
        if isinstance(payload, (dict, list, tuple)):
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            self._write_payload(path, payload)
        if self.persistence is not None:
            artifact_id = f"{execution_id}:{filename}"
            self.persistence.save_artifact_metadata(
                artifact_id,
                {"artifact_id": artifact_id, "execution_id": execution_id, "artifact_type": filename, "artifact_path": str(path)},
                execution_id=execution_id,
            )
        return path

    def get_artifact(self, execution_id: str, filename: str) -> str:
        self._validate_name(execution_id, label="execution id")
        self._validate_name(filename, label="artifact filename")
        path = self._assert_managed(self.artifacts_root, self.artifacts_root / execution_id / filename)
        return path.read_text(encoding="utf-8")

    def list_artifacts(self, execution_id: str | None = None) -> tuple[Path, ...]:
        if execution_id is not None:
            self._validate_name(execution_id, label="execution id")
            roots = [self.artifacts_root / execution_id]
        else:
            roots = [p for p in self.artifacts_root.iterdir() if p.is_dir() and not p.is_symlink()]
        files: list[Path] = []
        for root in roots:
            if root.is_dir():
                files.extend(p for p in root.iterdir() if p.is_file() and not p.is_symlink())
        return tuple(sorted(files, key=lambda p: str(p)))

    def save_log(self, execution_id: str, log_name: str, payload: bytes | str) -> Path:
        self._validate_name(execution_id, label="execution id")
        name = log_name[:-4] if log_name.endswith(".log") else log_name
        self._validate_name(name, label="log name")
        root = self._assert_managed(self.logs_root, self.logs_root / execution_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{name}.log"
        text = payload.decode("utf-8") if isinstance(payload, bytes) else str(payload)
        if not text.endswith("\n"):
            text += "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def get_logs(self, execution_id: str, log_name: str | None = None) -> str | dict[str, str]:
        self._validate_name(execution_id, label="execution id")
        root = self._assert_managed(self.logs_root, self.logs_root / execution_id)
        if log_name is not None:
            name = log_name[:-4] if log_name.endswith(".log") else log_name
            self._validate_name(name, label="log name")
            return (root / f"{name}.log").read_text(encoding="utf-8")
        result: dict[str, str] = {}
        if root.is_dir():
            for path in sorted(root.glob("*.log")):
                result[path.stem] = path.read_text(encoding="utf-8")
        return result

    def list_logs(self, execution_id: str | None = None) -> tuple[Path, ...]:
        if execution_id is not None:
            self._validate_name(execution_id, label="execution id")
            roots = [self.logs_root / execution_id]
        else:
            roots = [p for p in self.logs_root.iterdir() if p.is_dir() and not p.is_symlink()]
        files: list[Path] = []
        for root in roots:
            if root.is_dir():
                files.extend(p for p in root.glob("*.log") if p.is_file() and not p.is_symlink())
        return tuple(sorted(files, key=lambda p: str(p)))

    def cleanup_builds(self, days: int) -> int:
        return self._cleanup(self.builds_root, days)

    def cleanup_artifacts(self, days: int) -> int:
        return self._cleanup(self.artifacts_root, days)

    def cleanup_logs(self, days: int) -> int:
        return self._cleanup(self.logs_root, days)

    def _cleanup(self, root: Path, days: int) -> int:
        cutoff = self._now() - timedelta(days=days)
        removed = 0
        for path in root.iterdir():
            if not path.is_dir() or path.is_symlink():
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(path)
                removed += 1
        return removed

    def workspace_health(self) -> dict[str, Any]:
        total = 0
        for path in self.workspace_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return {
            "project_count": len(self.list_projects()),
            "build_count": len(self.list_builds()),
            "artifact_count": len(self.list_artifacts()),
            "log_count": len(self.list_logs()),
            "total_size_bytes": total,
        }

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @staticmethod
    def _write_payload(path: Path, payload: Any) -> None:
        if isinstance(payload, bytes):
            path.write_bytes(payload)
        elif isinstance(payload, Path):
            path.write_bytes(payload.read_bytes())
        elif payload is None:
            path.write_bytes(b"")
        else:
            path.write_text(str(payload), encoding="utf-8")
