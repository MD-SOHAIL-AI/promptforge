"""Safe filesystem management for the PromptForge runtime workspace."""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from ..observability.runtime_logs import RuntimeLogger
from ..state.persistence import PersistenceManager
from ..utils.paths import PathManager

__all__ = [
    "WorkspaceConflictError",
    "WorkspaceError",
    "WorkspaceManager",
    "WorkspaceNotFoundError",
    "WorkspacePathError",
]


_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_LOG_NAMES = frozenset({"workflow", "build", "flash", "monitor"})
_BUILD_NAMES = frozenset({"firmware.bin", "firmware.elf"})
_ARTIFACT_NAMES = frozenset(
    {"generated_files.json", "validation.json", "build_report.json"}
)
_METADATA_NAME = "metadata.json"
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class WorkspaceError(RuntimeError):
    """Base error for managed workspace operations."""


class WorkspaceNotFoundError(WorkspaceError, FileNotFoundError):
    """A requested managed workspace entry does not exist."""


class WorkspaceConflictError(WorkspaceError, FileExistsError):
    """A workspace operation would overwrite an owned entry."""


class WorkspacePathError(WorkspaceError, ValueError):
    """A supplied name could escape or corrupt the managed workspace."""


class WorkspaceManager:
    """Single filesystem boundary for PromptForge workspace operations.

    The filesystem is authoritative. ``PersistenceManager`` and
    ``RuntimeLogger`` are optional integration sinks used to mirror metadata
    and record lifecycle events after successful filesystem mutations.
    """

    def __init__(
        self,
        path_manager: PathManager | None = None,
        *,
        persistence: PersistenceManager | None = None,
        runtime_logger: RuntimeLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if path_manager is not None and not isinstance(path_manager, PathManager):
            raise TypeError("path_manager must be a PathManager")
        if persistence is not None and not isinstance(
            persistence,
            PersistenceManager,
        ):
            raise TypeError("persistence must be a PersistenceManager")
        if runtime_logger is not None and not isinstance(
            runtime_logger,
            RuntimeLogger,
        ):
            raise TypeError("runtime_logger must be a RuntimeLogger")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        self._paths = path_manager or PathManager()
        self._persistence = persistence
        self._runtime_logger = runtime_logger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._workspace = self._paths.workspace_directory()
        _assert_safe_root(self._workspace)
        self._projects = self._paths.workspace_projects_directory()
        self._builds = self._paths.workspace_builds_directory()
        self._artifacts = self._paths.workspace_artifacts_directory()
        self._logs = self._paths.workspace_logs_directory()
        for root in (
            self._projects,
            self._builds,
            self._artifacts,
            self._logs,
        ):
            _assert_safe_root(root, parent=self._workspace)

    @property
    def workspace_root(self) -> Path:
        """Return the absolute managed workspace root."""

        return self._workspace

    def create_project(
        self,
        project_name: str,
        *,
        project_id: str | None = None,
        task_id: str = "UNKNOWN",
        execution_id: str = "UNKNOWN",
        target_board: str = "UNKNOWN",
        framework: str = "UNKNOWN",
        status: str = "CREATED",
        platformio_ini: str = "",
    ) -> dict[str, Any]:
        """Create an empty managed project skeleton and metadata atomically."""

        name = _project_name(project_name)
        resolved_project_id = project_id or f"project-{uuid.uuid4().hex}"
        for value, label in (
            (resolved_project_id, "project_id"),
            (task_id, "task_id"),
            (execution_id, "execution_id"),
        ):
            _identifier(value, label)
        for value, label in (
            (target_board, "target_board"),
            (framework, "framework"),
            (status, "status"),
        ):
            _text(value, label)
        if not isinstance(platformio_ini, str) or "\x00" in platformio_ini:
            raise ValueError("platformio_ini must be text without NUL characters")

        with self._lock:
            target = _child(self._projects, name)
            if target.exists() or target.is_symlink():
                raise WorkspaceConflictError(f"project already exists: {name}")
            now = self._now()
            metadata = {
                "project_id": resolved_project_id,
                "task_id": task_id,
                "execution_id": execution_id,
                "created_at": _format_datetime(now),
                "updated_at": _format_datetime(now),
                "target_board": target_board,
                "framework": framework,
                "status": status,
            }
            stage = _child(
                self._projects,
                f".{name}.{uuid.uuid4().hex}.tmp",
            )
            try:
                stage.mkdir(parents=False, exist_ok=False)
                (stage / "src").mkdir()
                (stage / "include").mkdir()
                _write_text(stage / "platformio.ini", platformio_ini)
                _write_json(stage / _METADATA_NAME, metadata)
                stage.replace(target)
            except BaseException:
                _remove_tree(stage)
                raise
            self._persist_project(name, metadata)
            self._log_event(
                "project created",
                task_id=task_id,
                execution_id=execution_id,
                metadata={"project_name": name, "project_id": resolved_project_id},
            )
            return _project_record(name, target, metadata)

    def delete_project(self, project_name: str) -> bool:
        """Delete a managed project and return whether it existed."""

        name = _project_name(project_name)
        with self._lock:
            target = _child(self._projects, name)
            if not target.exists():
                return False
            _assert_managed_directory(target, self._projects)
            metadata = self._read_metadata(target)
            _remove_tree(target)
            self._log_event(
                "project deleted",
                task_id=metadata["task_id"],
                execution_id=metadata["execution_id"],
                metadata={"project_name": name},
            )
            return True

    def archive_project(
        self,
        project_name: str,
        *,
        execution_id: str | None = None,
    ) -> Path:
        """Create a deterministic ZIP snapshot of a managed project."""

        name = _project_name(project_name)
        with self._lock:
            source = self._require_project(name)
            metadata = self._read_metadata(source)
            selected_execution = execution_id or metadata["execution_id"]
            if selected_execution == "UNKNOWN":
                selected_execution = f"archive-{uuid.uuid4().hex}"
            _identifier(selected_execution, "execution_id")
            archived_metadata = {
                **metadata,
                "execution_id": selected_execution,
                "updated_at": _format_datetime(self._now()),
                "status": "ARCHIVED",
            }
            destination_root = self._execution_directory(
                self._artifacts,
                selected_execution,
                create=True,
            )
            destination = _child(destination_root, f"{name}.zip")
            temporary = _child(
                destination_root,
                f".{name}.{uuid.uuid4().hex}.tmp",
            )
            try:
                with ZipFile(temporary, "w", ZIP_DEFLATED) as archive:
                    for path in sorted(source.rglob("*")):
                        if path.is_symlink():
                            raise WorkspacePathError(
                                "project archives cannot contain symlinks"
                            )
                        if path.is_file():
                            archive_name = Path(name) / path.relative_to(source)
                            if path.name == _METADATA_NAME:
                                archive.writestr(
                                    str(archive_name),
                                    json.dumps(
                                        archived_metadata,
                                        ensure_ascii=True,
                                        allow_nan=False,
                                        sort_keys=True,
                                        indent=2,
                                    )
                                    + "\n",
                                )
                            else:
                                archive.write(path, archive_name)
                temporary.replace(destination)
                _write_json(source / _METADATA_NAME, archived_metadata)
            finally:
                temporary.unlink(missing_ok=True)
            self._persist_project(name, archived_metadata)
            self._persist_artifact(
                selected_execution,
                destination.name,
                destination,
            )
            self._log_event(
                "project archived",
                task_id=archived_metadata["task_id"],
                execution_id=selected_execution,
                metadata={"project_name": name, "archive_path": str(destination)},
            )
            return destination

    def list_projects(self) -> tuple[dict[str, Any], ...]:
        """Return valid project records in deterministic name order."""

        with self._lock:
            records: list[dict[str, Any]] = []
            for path in _managed_directories(self._projects):
                try:
                    name = _project_name(path.name)
                    records.append(
                        _project_record(name, path, self._read_metadata(path))
                    )
                except (WorkspaceError, ValueError, OSError, json.JSONDecodeError):
                    continue
            return tuple(records)

    def project_exists(self, project_name: str) -> bool:
        """Return whether a valid managed project exists."""

        name = _project_name(project_name)
        with self._lock:
            target = _child(self._projects, name)
            if not target.is_dir() or target.is_symlink():
                return False
            try:
                self._read_metadata(target)
            except (WorkspaceError, ValueError, OSError, json.JSONDecodeError):
                return False
            return True

    def project_metadata(self, project_name: str) -> dict[str, Any]:
        """Return detached metadata for one managed project."""

        name = _project_name(project_name)
        with self._lock:
            return dict(self._read_metadata(self._require_project(name)))

    def save_build(
        self,
        execution_id: str,
        files: Mapping[str, bytes | str | Path] | str,
        content: bytes | str | Path | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store firmware build files and execution metadata atomically."""

        identifier = _identifier(execution_id, "execution_id")
        payloads = _payload_mapping(files, content, allowed=_BUILD_NAMES)
        supplied_metadata = _json_mapping(metadata or {}, "metadata")
        with self._lock:
            target = self._execution_directory(self._builds, identifier, create=True)
            now = self._now()
            existing = _read_optional_json(target / _METADATA_NAME)
            created_at = existing.get("created_at", _format_datetime(now))
            build_metadata = {
                **supplied_metadata,
                "project_id": supplied_metadata.get("project_id", "UNKNOWN"),
                "task_id": supplied_metadata.get("task_id", "UNKNOWN"),
                "execution_id": identifier,
                "created_at": created_at,
                "updated_at": _format_datetime(now),
                "target_board": supplied_metadata.get(
                    "target_board",
                    "UNKNOWN",
                ),
                "framework": supplied_metadata.get("framework", "UNKNOWN"),
                "status": supplied_metadata.get("status", "SAVED"),
                "files": sorted(payloads),
            }
            for filename, value in payloads.items():
                _write_payload(_child(target, filename), value)
            _write_json(target / _METADATA_NAME, build_metadata)
            self._persist_build(identifier, target, build_metadata)
            self._log_build(identifier, build_metadata)
            return _storage_record(identifier, target, build_metadata)

    def get_build(
        self,
        execution_id: str,
        filename: str | None = None,
    ) -> dict[str, Any] | bytes:
        """Return build metadata, or bytes for one named build file."""

        identifier = _identifier(execution_id, "execution_id")
        with self._lock:
            target = self._execution_directory(self._builds, identifier)
            if filename is not None:
                name = _allowed_filename(filename, _BUILD_NAMES)
                return _read_bytes(_child(target, name))
            metadata = _read_required_json(target / _METADATA_NAME)
            return _storage_record(identifier, target, metadata)

    def list_builds(self) -> tuple[dict[str, Any], ...]:
        """Return build records ordered by creation time and identity."""

        with self._lock:
            records = self._list_storage(self._builds)
            return tuple(sorted(records, key=_record_sort_key))

    def latest_build(self) -> dict[str, Any] | None:
        """Return the newest build record, or ``None`` when no build exists."""

        builds = self.list_builds()
        return builds[-1] if builds else None

    def save_artifact(
        self,
        execution_id: str,
        artifact_name: str,
        content: bytes | str | Path | Mapping[str, Any],
    ) -> Path:
        """Store one canonical execution artifact atomically."""

        identifier = _identifier(execution_id, "execution_id")
        name = _allowed_filename(artifact_name, _ARTIFACT_NAMES)
        with self._lock:
            target_root = self._execution_directory(
                self._artifacts,
                identifier,
                create=True,
            )
            target = _child(target_root, name)
            if isinstance(content, Mapping):
                _write_json(target, _json_mapping(content, "content"))
            else:
                _write_payload(target, content)
            self._persist_artifact(identifier, name, target)
            return target

    def get_artifact(self, execution_id: str, artifact_name: str) -> bytes:
        """Return the exact bytes of one stored artifact."""

        identifier = _identifier(execution_id, "execution_id")
        name = _allowed_filename(artifact_name, _ARTIFACT_NAMES)
        with self._lock:
            root = self._execution_directory(self._artifacts, identifier)
            return _read_bytes(_child(root, name))

    def list_artifacts(
        self,
        execution_id: str | None = None,
    ) -> tuple[Path, ...]:
        """Return stored artifact paths in deterministic order."""

        with self._lock:
            return self._list_files(self._artifacts, execution_id)

    def save_log(
        self,
        execution_id: str,
        log_type: str,
        content: str | bytes | None = None,
    ) -> Path:
        """Append text to one execution log.

        When ``content`` is omitted, retained ``RuntimeLogger`` entries are
        exported as JSON Lines.
        """

        identifier = _identifier(execution_id, "execution_id")
        normalized_type = _log_type(log_type)
        if content is None:
            if self._runtime_logger is None:
                raise ValueError(
                    "content is required when no RuntimeLogger is configured"
                )
            content = self._runtime_logger.export_logs(format="jsonl")
        if isinstance(content, bytes):
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("log content must be UTF-8") from exc
        elif isinstance(content, str):
            text = content
        else:
            raise TypeError("log content must be text or UTF-8 bytes")
        if "\x00" in text:
            raise ValueError("log content cannot contain NUL characters")

        with self._lock:
            root = self._execution_directory(self._logs, identifier, create=True)
            destination = _child(root, f"{normalized_type}.log")
            existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
            addition = text.rstrip("\n")
            combined = existing
            if addition:
                combined += addition + "\n"
            _write_text(destination, combined)
            return destination

    def get_logs(
        self,
        execution_id: str,
        log_type: str | None = None,
    ) -> dict[str, str] | str:
        """Return all execution logs, or one requested log as text."""

        identifier = _identifier(execution_id, "execution_id")
        with self._lock:
            root = self._execution_directory(self._logs, identifier)
            if log_type is not None:
                name = _log_type(log_type)
                path = _child(root, f"{name}.log")
                if not path.is_file():
                    raise WorkspaceNotFoundError(f"log does not exist: {name}")
                return path.read_text(encoding="utf-8")
            return {
                path.stem: path.read_text(encoding="utf-8")
                for path in sorted(root.glob("*.log"))
                if path.is_file() and not path.is_symlink()
            }

    def list_logs(self, execution_id: str | None = None) -> tuple[Path, ...]:
        """Return stored log paths in deterministic order."""

        with self._lock:
            return self._list_files(self._logs, execution_id, suffix=".log")

    def cleanup_logs(self, days_to_keep: int) -> int:
        """Delete expired execution log directories and return their count."""

        return self._cleanup(self._logs, days_to_keep)

    def cleanup_builds(self, days_to_keep: int) -> int:
        """Delete expired build directories and return their count."""

        return self._cleanup(self._builds, days_to_keep)

    def cleanup_artifacts(self, days_to_keep: int) -> int:
        """Delete expired artifact directories and return their count."""

        return self._cleanup(self._artifacts, days_to_keep)

    def workspace_health(self) -> dict[str, int]:
        """Return counts and total stored byte size for the workspace."""

        with self._lock:
            return {
                "project_count": len(_managed_directories(self._projects)),
                "artifact_count": len(self.list_artifacts()),
                "build_count": len(_managed_directories(self._builds)),
                "log_count": len(self.list_logs()),
                "total_size_bytes": sum(
                    path.stat().st_size
                    for root in (
                        self._projects,
                        self._builds,
                        self._artifacts,
                        self._logs,
                    )
                    for path in root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                ),
            }

    def _require_project(self, project_name: str) -> Path:
        target = _child(self._projects, project_name)
        if not target.is_dir() or target.is_symlink():
            raise WorkspaceNotFoundError(
                f"project does not exist: {project_name}"
            )
        _assert_managed_directory(target, self._projects)
        return target

    def _read_metadata(self, project_path: Path) -> dict[str, Any]:
        metadata = _read_required_json(project_path / _METADATA_NAME)
        expected = {
            "project_id",
            "task_id",
            "execution_id",
            "created_at",
            "updated_at",
            "target_board",
            "framework",
            "status",
        }
        if set(metadata) != expected:
            raise WorkspaceError("project metadata schema is invalid")
        return metadata

    def _execution_directory(
        self,
        root: Path,
        execution_id: str,
        *,
        create: bool = False,
    ) -> Path:
        target = _child(root, execution_id)
        if create:
            if target.is_symlink():
                raise WorkspacePathError("managed directory cannot be a symlink")
            target.mkdir(parents=False, exist_ok=True)
        if not target.is_dir() or target.is_symlink():
            raise WorkspaceNotFoundError(
                f"execution workspace does not exist: {execution_id}"
            )
        _assert_managed_directory(target, root)
        return target

    def _list_storage(self, root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in _managed_directories(root):
            try:
                metadata = _read_required_json(path / _METADATA_NAME)
            except (WorkspaceError, OSError, json.JSONDecodeError):
                continue
            records.append(_storage_record(path.name, path, metadata))
        return records

    def _list_files(
        self,
        root: Path,
        execution_id: str | None,
        *,
        suffix: str | None = None,
    ) -> tuple[Path, ...]:
        roots: tuple[Path, ...]
        if execution_id is None:
            roots = _managed_directories(root)
        else:
            identifier = _identifier(execution_id, "execution_id")
            roots = (self._execution_directory(root, identifier),)
        files = [
            path
            for directory in roots
            for path in directory.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.name != _METADATA_NAME
            and (suffix is None or path.suffix == suffix)
        ]
        return tuple(sorted(files, key=lambda item: str(item).casefold()))

    def _cleanup(self, root: Path, days_to_keep: int) -> int:
        if (
            not isinstance(days_to_keep, int)
            or isinstance(days_to_keep, bool)
            or days_to_keep < 0
        ):
            raise ValueError("days_to_keep must be a non-negative integer")
        cutoff = self._now() - timedelta(days=days_to_keep)
        removed = 0
        with self._lock:
            for path in _managed_directories(root):
                modified = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=timezone.utc,
                )
                if modified < cutoff:
                    _remove_tree(path)
                    removed += 1
        return removed

    def _persist_project(
        self,
        project_name: str,
        metadata: Mapping[str, Any],
    ) -> None:
        if self._persistence is not None:
            self._persistence.save_project(
                {**metadata, "project_name": project_name}
            )

    def _persist_build(
        self,
        execution_id: str,
        path: Path,
        metadata: Mapping[str, Any],
    ) -> None:
        if self._persistence is not None:
            self._persistence.save_build(
                {
                    **metadata,
                    "build_id": execution_id,
                    "path": str(path),
                },
                build_id=execution_id,
                execution_id=execution_id,
            )

    def _persist_artifact(
        self,
        execution_id: str,
        artifact_name: str,
        path: Path,
    ) -> None:
        if self._persistence is not None:
            artifact_id = f"{execution_id}:{artifact_name}"
            self._persistence.save_artifact_metadata(
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_name,
                    "artifact_path": str(path),
                },
                artifact_id=artifact_id,
                execution_id=execution_id,
            )

    def _log_event(
        self,
        message: str,
        *,
        task_id: str,
        execution_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        if self._runtime_logger is not None:
            self._runtime_logger.log_workflow(
                message,
                task_id=None if task_id == "UNKNOWN" else task_id,
                execution_id=(
                    None if execution_id == "UNKNOWN" else execution_id
                ),
                metadata=metadata,
            )

    def _log_build(
        self,
        execution_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        if self._runtime_logger is not None:
            self._runtime_logger.log_build(
                "build saved",
                execution_id=execution_id,
                metadata=metadata,
            )

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise TypeError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _project_name(value: object) -> str:
    if not isinstance(value, str) or not _PROJECT_NAME_RE.fullmatch(value):
        raise WorkspacePathError(
            "project_name must use letters, digits, '.', '_' or '-'"
        )
    if value in {".", ".."} or value.endswith((".", " ")):
        raise WorkspacePathError("project_name is invalid")
    _reject_reserved_name(value, "project_name")
    return value


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise WorkspacePathError(f"{field_name} is invalid")
    if value in {".", ".."}:
        raise WorkspacePathError(f"{field_name} is invalid")
    _reject_reserved_name(value, field_name)
    return value


def _reject_reserved_name(value: str, field_name: str) -> None:
    stem = value.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise WorkspacePathError(f"{field_name} is reserved by the operating system")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field_name} must be non-empty text")
    return value


def _allowed_filename(value: object, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise WorkspacePathError(
            "filename must be one of: " + ", ".join(sorted(allowed))
        )
    return value


def _log_type(value: object) -> str:
    if not isinstance(value, str):
        raise WorkspacePathError("log_type must be a string")
    normalized = value.removesuffix(".log")
    if normalized not in _LOG_NAMES:
        raise WorkspacePathError(
            "log_type must be one of: " + ", ".join(sorted(_LOG_NAMES))
        )
    return normalized


def _payload_mapping(
    files: Mapping[str, bytes | str | Path] | str,
    content: bytes | str | Path | None,
    *,
    allowed: frozenset[str],
) -> dict[str, bytes | str | Path]:
    if isinstance(files, str):
        if content is None:
            raise ValueError("content is required for a single build file")
        return {_allowed_filename(files, allowed): content}
    if content is not None:
        raise ValueError("content must be omitted when files is a mapping")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("files must be a non-empty mapping")
    return {
        _allowed_filename(name, allowed): value
        for name, value in files.items()
    }


def _json_mapping(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be JSON-compatible") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{field_name} must be a mapping")
    return decoded


def _child(root: Path, name: str) -> Path:
    candidate = root / name
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError("path escapes the managed workspace") from exc
    return candidate


def _assert_safe_root(root: Path, *, parent: Path | None = None) -> None:
    if root.is_symlink() or not root.is_dir():
        raise WorkspacePathError(f"workspace root is unsafe: {root}")
    if parent is not None:
        try:
            root.resolve().relative_to(parent.resolve())
        except ValueError as exc:
            raise WorkspacePathError(
                f"workspace root escapes its parent: {root}"
            ) from exc


def _assert_managed_directory(path: Path, root: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise WorkspacePathError(f"managed path is unsafe: {path}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise WorkspacePathError("managed path escapes its workspace root") from exc


def _managed_directories(root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in root.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and not path.name.startswith(".")
            ),
            key=lambda item: item.name.casefold(),
        )
    )


def _write_payload(destination: Path, value: bytes | str | Path) -> None:
    if isinstance(value, Path):
        if not value.is_file():
            raise FileNotFoundError(f"payload source does not exist: {value}")
        data = value.read_bytes()
    elif isinstance(value, bytes):
        data = value
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        raise TypeError("payload must be bytes, text, or a Path")
    _write_bytes(destination, data)


def _write_bytes(destination: Path, content: bytes) -> None:
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_text(destination: Path, content: str) -> None:
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(destination: Path, value: Mapping[str, Any]) -> None:
    content = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    )
    _write_text(destination, content + "\n")


def _read_bytes(path: Path) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise WorkspaceNotFoundError(f"workspace file does not exist: {path.name}")
    return path.read_bytes()


def _read_required_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise WorkspaceNotFoundError(f"metadata does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise WorkspaceError(f"JSON object required: {path}")
    return value


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_required_json(path)


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise WorkspacePathError("refusing to remove a symlinked workspace path")
    shutil.rmtree(path)


def _project_record(
    project_name: str,
    path: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "project_name": project_name,
        "project_path": str(path),
        **dict(metadata),
    }


def _storage_record(
    execution_id: str,
    path: Path,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "execution_id": execution_id,
        "path": str(path),
        "metadata": dict(metadata),
    }


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    metadata = record.get("metadata", {})
    created_at = metadata.get("created_at", "") if isinstance(metadata, Mapping) else ""
    return str(created_at), str(record.get("execution_id", ""))


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
