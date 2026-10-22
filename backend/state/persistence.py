"""Thread-safe SQLite persistence for PromptForge state.

The database stores canonical JSON snapshots alongside a small set of indexed
columns used for lookup and diagnostics.  Domain services remain responsible
for validating and reconstructing richer application objects.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TypeAlias, overload

__all__ = [
    "PersistenceError",
    "PersistenceManager",
    "SCHEMA_SQL",
    "SCHEMA_TABLES",
    "SCHEMA_VERSION",
]


SCHEMA_VERSION = 1

SCHEMA_TABLES: Mapping[str, tuple[str, ...]] = {
    "projects": (
        "project_id",
        "project_name",
        "target_board",
        "framework",
        "payload_json",
        "created_at",
        "updated_at",
    ),
    "executions": (
        "execution_id",
        "project_id",
        "status",
        "payload_json",
        "created_at",
        "updated_at",
    ),
    "builds": (
        "build_id",
        "project_id",
        "execution_id",
        "status",
        "payload_json",
        "created_at",
        "updated_at",
    ),
    "validation_results": (
        "validation_id",
        "project_id",
        "execution_id",
        "validator",
        "is_valid",
        "payload_json",
        "created_at",
        "updated_at",
    ),
    "artifact_metadata": (
        "artifact_id",
        "project_id",
        "execution_id",
        "build_id",
        "artifact_type",
        "artifact_path",
        "payload_json",
        "created_at",
        "updated_at",
    ),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    project_name TEXT,
    target_board TEXT,
    framework TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    project_id TEXT,
    status TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS builds (
    build_id TEXT PRIMARY KEY,
    project_id TEXT,
    execution_id TEXT,
    status TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id TEXT PRIMARY KEY,
    project_id TEXT,
    execution_id TEXT,
    validator TEXT,
    is_valid INTEGER CHECK (is_valid IN (0, 1) OR is_valid IS NULL),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_metadata (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT,
    execution_id TEXT,
    build_id TEXT,
    artifact_type TEXT,
    artifact_path TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_name
    ON projects(project_name, project_id);
CREATE INDEX IF NOT EXISTS idx_executions_project
    ON executions(project_id, created_at, execution_id);
CREATE INDEX IF NOT EXISTS idx_builds_project
    ON builds(project_id, created_at, build_id);
CREATE INDEX IF NOT EXISTS idx_builds_execution
    ON builds(execution_id, created_at, build_id);
CREATE INDEX IF NOT EXISTS idx_validations_project
    ON validation_results(project_id, created_at, validation_id);
CREATE INDEX IF NOT EXISTS idx_validations_execution
    ON validation_results(execution_id, created_at, validation_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_project
    ON artifact_metadata(project_id, created_at, artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_execution
    ON artifact_metadata(execution_id, created_at, artifact_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_build
    ON artifact_metadata(build_id, created_at, artifact_id);
"""


class PersistenceError(RuntimeError):
    """Raised when persistent state cannot be safely read or written."""


class _Serializable(Protocol):
    def to_dict(self) -> Mapping[str, Any]: ...


RecordInput: TypeAlias = Mapping[str, Any] | _Serializable | object
TransactionMode: TypeAlias = Literal["DEFERRED", "IMMEDIATE", "EXCLUSIVE"]


class PersistenceManager:
    """Own a versioned SQLite database containing PromptForge snapshots.

    One connection is shared under a re-entrant lock.  This preserves
    ``:memory:`` semantics and makes transactions atomic across threads.
    Nested transactions use SQLite savepoints.
    """

    def __init__(
        self,
        database_path: str | Path = ".promptforge/promptforge.db",
        *,
        timeout: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(database_path, (str, Path)):
            raise TypeError("database_path must be a string or Path")
        if isinstance(database_path, str) and not database_path.strip():
            raise ValueError("database_path must be non-empty")
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        raw_path = str(database_path)
        self._database_path = (
            raw_path
            if raw_path == ":memory:" or raw_path.startswith("file:")
            else str(Path(raw_path).expanduser())
        )
        self._timeout = float(timeout)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._local = threading.local()
        self._connection: sqlite3.Connection | None = None
        self._initialized = False

    @property
    def database_path(self) -> str:
        return self._database_path

    def initialize(self) -> None:
        """Create or validate the database schema atomically."""

        with self._lock:
            if self._initialized:
                return
            connection = self._connect_locked()
            try:
                current_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if current_version > SCHEMA_VERSION:
                    raise PersistenceError(
                        "database schema is newer than this application "
                        f"({current_version} > {SCHEMA_VERSION})"
                    )
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in SCHEMA_SQL.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                    if not _schema_is_valid(connection):
                        raise PersistenceError(
                            "database schema does not match PromptForge schema"
                        )
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                self._initialized = True
            except PersistenceError:
                raise
            except sqlite3.Error as exc:
                self._recover_connection_locked()
                raise PersistenceError("failed to initialize persistence") from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield the configured connection while preventing concurrent use."""

        self.initialize()
        with self._lock:
            yield self._connect_locked()

    @contextmanager
    def transaction(
        self,
        mode: TransactionMode = "IMMEDIATE",
    ) -> Iterator[sqlite3.Connection]:
        """Yield an atomic transaction, using savepoints when nested."""

        if mode not in {"DEFERRED", "IMMEDIATE", "EXCLUSIVE"}:
            raise ValueError("unsupported transaction mode")
        self.initialize()
        with self._lock:
            connection = self._connect_locked()
            depth = getattr(self._local, "transaction_depth", 0)
            savepoint = f"promptforge_sp_{depth}"
            try:
                if depth == 0:
                    connection.execute(f"BEGIN {mode}")
                else:
                    connection.execute(f"SAVEPOINT {savepoint}")
                self._local.transaction_depth = depth + 1
                yield connection
                if depth == 0:
                    connection.commit()
                else:
                    connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                try:
                    if depth == 0:
                        connection.rollback()
                    else:
                        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                except sqlite3.Error:
                    self._recover_connection_locked()
                raise
            finally:
                self._local.transaction_depth = depth

    @overload
    def save_project(
        self,
        project: RecordInput,
        /,
        *,
        project_id: str | None = None,
    ) -> str: ...

    @overload
    def save_project(self, project: str, data: RecordInput, /) -> str: ...

    def save_project(
        self,
        project: RecordInput | str,
        data: RecordInput | None = None,
        *,
        project_id: str | None = None,
    ) -> str:
        payload, record_id = _prepare_record(
            project, data, project_id, ("project_id",), "project"
        )
        now = self._now()
        created_at = _payload_time(payload, "created_at", now)
        self._upsert(
            "projects",
            "project_id",
            record_id,
            payload,
            {
                "project_name": _optional_text(payload.get("project_name")),
                "target_board": _optional_text(payload.get("target_board")),
                "framework": _optional_text(payload.get("framework")),
            },
            created_at,
            now,
        )
        return record_id

    def load_project(self, project_id: str) -> dict[str, Any] | None:
        return self._load("projects", "project_id", project_id)

    def list_projects(self) -> tuple[dict[str, Any], ...]:
        """Return project snapshots ordered deterministically by identity."""

        self.initialize()
        try:
            with self.connection() as connection:
                rows = connection.execute(
                    "SELECT payload_json FROM projects "
                    "ORDER BY COALESCE(project_name, ''), project_id"
                ).fetchall()
            return tuple(_decode_payload(row[0]) for row in rows)
        except sqlite3.Error as exc:
            raise PersistenceError("failed to list projects") from exc

    @overload
    def save_execution(
        self,
        execution: RecordInput,
        /,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
    ) -> str: ...

    @overload
    def save_execution(
        self,
        execution: str,
        data: RecordInput,
        /,
        *,
        project_id: str | None = None,
    ) -> str: ...

    def save_execution(
        self,
        execution: RecordInput | str,
        data: RecordInput | None = None,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        payload, record_id = _prepare_record(
            execution,
            data,
            execution_id,
            ("execution_id", "task_id", "session_id"),
            "execution",
            derived_prefix="execution",
        )
        now = self._now()
        self._upsert(
            "executions",
            "execution_id",
            record_id,
            payload,
            {
                "project_id": _coalesce_text(
                    project_id,
                    payload.get("project_id"),
                ),
                "status": _optional_text(payload.get("status")),
            },
            _payload_time(payload, "created_at", now),
            now,
        )
        return record_id

    def load_execution(self, execution_id: str) -> dict[str, Any] | None:
        return self._load("executions", "execution_id", execution_id)

    @overload
    def save_build(
        self,
        build: RecordInput,
        /,
        *,
        build_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
    ) -> str: ...

    @overload
    def save_build(
        self,
        build: str,
        data: RecordInput,
        /,
        *,
        project_id: str | None = None,
        execution_id: str | None = None,
    ) -> str: ...

    def save_build(
        self,
        build: RecordInput | str,
        data: RecordInput | None = None,
        *,
        build_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
    ) -> str:
        payload, record_id = _prepare_record(
            build,
            data,
            build_id,
            ("build_id",),
            "build",
            derived_prefix="build",
        )
        now = self._now()
        self._upsert(
            "builds",
            "build_id",
            record_id,
            payload,
            {
                "project_id": _coalesce_text(
                    project_id,
                    payload.get("project_id"),
                ),
                "execution_id": _coalesce_text(
                    execution_id,
                    payload.get("execution_id"),
                ),
                "status": _optional_text(payload.get("status")),
            },
            _payload_time(payload, "created_at", now),
            now,
        )
        return record_id

    def load_build(self, build_id: str) -> dict[str, Any] | None:
        return self._load("builds", "build_id", build_id)

    def save_validation(
        self,
        validation: RecordInput | str,
        data: RecordInput | None = None,
        *,
        validation_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
        validator: str | None = None,
    ) -> str:
        payload, record_id = _prepare_record(
            validation,
            data,
            validation_id,
            ("validation_id",),
            "validation",
            derived_prefix="validation",
        )
        now = self._now()
        valid = _first_boolean(
            payload, ("valid", "is_valid", "compatible", "safe_to_continue")
        )
        self._upsert(
            "validation_results",
            "validation_id",
            record_id,
            payload,
            {
                "project_id": _coalesce_text(
                    project_id,
                    payload.get("project_id"),
                ),
                "execution_id": _coalesce_text(
                    execution_id,
                    payload.get("execution_id"),
                ),
                "validator": _coalesce_text(validator, payload.get("validator")),
                "is_valid": None if valid is None else int(valid),
            },
            _payload_time(payload, "created_at", now),
            now,
        )
        return record_id

    def save_artifact_metadata(
        self,
        artifact: RecordInput | str,
        data: RecordInput | None = None,
        *,
        artifact_id: str | None = None,
        project_id: str | None = None,
        execution_id: str | None = None,
        build_id: str | None = None,
    ) -> str:
        payload, record_id = _prepare_record(
            artifact, data, artifact_id, ("artifact_id",), "artifact metadata"
        )
        now = self._now()
        self._upsert(
            "artifact_metadata",
            "artifact_id",
            record_id,
            payload,
            {
                "project_id": _coalesce_text(
                    project_id,
                    payload.get("project_id"),
                ),
                "execution_id": _coalesce_text(
                    execution_id,
                    payload.get("execution_id"),
                ),
                "build_id": _coalesce_text(build_id, payload.get("build_id")),
                "artifact_type": _optional_text(payload.get("artifact_type")),
                "artifact_path": _coalesce_text(
                    _optional_text(payload.get("artifact_path")),
                    payload.get("path"),
                ),
            },
            _payload_time(payload, "created_at", now),
            now,
        )
        return record_id

    def health_check(self) -> bool:
        """Return whether SQLite and every required table are healthy."""

        try:
            self.initialize()
            with self.connection() as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    return False
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if not set(SCHEMA_TABLES).issubset(names):
                    return False
                return _schema_is_valid(connection)
        except (PersistenceError, sqlite3.Error):
            return False

    def close(self) -> None:
        """Close the connection; a later operation reopens it safely."""

        with self._lock:
            if self._connection is not None:
                try:
                    if self._connection.in_transaction:
                        self._connection.rollback()
                    self._connection.close()
                finally:
                    self._connection = None
                    self._initialized = False

    def __enter__(self) -> PersistenceManager:
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _connect_locked(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        path = self._database_path
        if path != ":memory:" and not path.startswith("file:"):
            Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(
                path,
                timeout=self._timeout,
                isolation_level=None,
                check_same_thread=False,
                uri=path.startswith("file:"),
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                f"PRAGMA busy_timeout = {int(self._timeout * 1000)}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            if path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            self._connection = connection
            return connection
        except sqlite3.Error as exc:
            raise PersistenceError("failed to open persistence database") from exc

    def _recover_connection_locked(self) -> None:
        connection = self._connection
        self._connection = None
        self._initialized = False
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                pass

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime):
            raise PersistenceError("clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise PersistenceError("clock must return a timezone-aware datetime")
        return _format_datetime(value)

    def _load(
        self,
        table: str,
        id_column: str,
        record_id: str,
    ) -> dict[str, Any] | None:
        _validate_identifier(record_id, id_column)
        try:
            with self.connection() as connection:
                row = connection.execute(
                    f"SELECT payload_json FROM {table} WHERE {id_column} = ?",
                    (record_id,),
                ).fetchone()
            return None if row is None else _decode_payload(row[0])
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to load {table}") from exc

    def _upsert(
        self,
        table: str,
        id_column: str,
        record_id: str,
        payload: Mapping[str, Any],
        indexed: Mapping[str, str | int | None],
        created_at: str,
        updated_at: str,
    ) -> None:
        payload_json = _canonical_json(payload)
        columns = [
            id_column,
            *indexed,
            "payload_json",
            "created_at",
            "updated_at",
        ]
        values = [record_id, *indexed.values(), payload_json, created_at, updated_at]
        updates = [
            *(f"{column} = excluded.{column}" for column in indexed),
            "payload_json = excluded.payload_json",
            "updated_at = excluded.updated_at",
        ]
        placeholders = ", ".join("?" for _ in columns)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT({id_column}) DO UPDATE SET {', '.join(updates)}"
        )
        try:
            with self.transaction() as connection:
                connection.execute(sql, values)
        except PersistenceError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError(f"failed to save {table}") from exc


def _prepare_record(
    value_or_id: RecordInput | str,
    data: RecordInput | None,
    explicit_id: str | None,
    id_fields: Sequence[str],
    label: str,
    *,
    derived_prefix: str | None = None,
) -> tuple[dict[str, Any], str]:
    if data is not None:
        if not isinstance(value_or_id, str):
            raise TypeError(f"{label} identifier must be a string")
        if explicit_id is not None and explicit_id != value_or_id:
            raise ValueError(f"conflicting {label} identifiers")
        record_id = value_or_id
        raw = data
    else:
        raw = value_or_id
        record_id = explicit_id

    payload = _record_mapping(raw, label)
    payload_id = next(
        (
            candidate
            for field_name in id_fields
            if (candidate := _optional_text(payload.get(field_name))) is not None
        ),
        None,
    )
    if record_id is not None and payload_id is not None and record_id != payload_id:
        raise ValueError(f"{label} identifier does not match its payload")
    record_id = record_id or payload_id
    if record_id is None and derived_prefix is not None:
        digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        record_id = f"{derived_prefix}-{digest[:32]}"
    if record_id is None:
        raise ValueError(f"{label} identifier is required")
    _validate_identifier(record_id, f"{label} identifier")
    return payload, record_id


def _record_mapping(value: object, label: str) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    elif hasattr(value, "api_dict") and callable(value.api_dict):
        value = value.api_dict()
    elif hasattr(value, "model_dump") and callable(value.model_dump):
        value = value.model_dump(mode="json")
    value = _json_value(value, active=set())
    if not isinstance(value, dict):
        raise TypeError(f"{label} must serialize to a mapping")
    return value


def _json_value(value: object, *, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("persisted numbers must be finite")
        return value
    if isinstance(value, Enum):
        return _json_value(value.value, active=active)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("persisted datetimes must be timezone-aware")
        return _format_datetime(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("binary payloads must be stored as artifact content")

    identity = id(value)
    if identity in active:
        raise ValueError("persisted values cannot contain cycles")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError("persisted mapping keys must be non-empty strings")
                result[key] = _json_value(item, active=active)
            return result
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: _json_value(getattr(value, field.name), active=active)
                for field in fields(value)
                if not field.name.startswith("_")
            }
        if isinstance(value, Sequence) and not isinstance(value, str):
            return [_json_value(item, active=active) for item in value]
    finally:
        active.remove(identity)
    raise TypeError(f"unsupported persisted value: {type(value).__name__}")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_payload(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise PersistenceError("persisted payload is not text")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PersistenceError("persisted payload is invalid JSON") from exc
    if not isinstance(value, dict):
        raise PersistenceError("persisted payload is not an object")
    return value


def _schema_is_valid(connection: sqlite3.Connection) -> bool:
    for table, expected_columns in SCHEMA_TABLES.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual_columns = tuple(row[1] for row in rows)
        if actual_columns != expected_columns:
            return False
    return True


def _validate_identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(value) > 256 or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    raw = value.value if isinstance(value, Enum) else value
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return None
    return raw


def _coalesce_text(primary: object, secondary: object) -> str | None:
    return _optional_text(primary) or _optional_text(secondary)


def _payload_time(payload: Mapping[str, Any], key: str, fallback: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        return fallback
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return fallback
    return _format_datetime(parsed)


def _first_boolean(
    payload: Mapping[str, Any],
    keys: Sequence[str],
) -> bool | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    return None


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
