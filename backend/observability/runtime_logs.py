"""Structured runtime event logging for PromptForge.

Runtime logs are emitted through the standard :mod:`logging` package and kept
in a bounded in-memory history for deterministic JSON or JSON Lines export.
The module has no dependency on the API logging middleware.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
import uuid
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Literal, TypeAlias

__all__ = [
    "JSONValue",
    "LogExportFormat",
    "LogLevel",
    "RuntimeLog",
    "RuntimeLogType",
    "RuntimeLogger",
]


JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
RuntimeLogType: TypeAlias = Literal[
    "workflow",
    "generation",
    "build",
    "flash",
    "monitor",
]
LogLevel: TypeAlias = Literal[
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
]
LogExportFormat: TypeAlias = Literal["json", "jsonl"]

_LOG_TYPES: Final[frozenset[str]] = frozenset(
    {"workflow", "generation", "build", "flash", "monitor"}
)
_LOG_LEVELS: Final[Mapping[str, int]] = MappingProxyType(
    {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
)


@dataclass(frozen=True, slots=True)
class RuntimeLog:
    """One immutable, JSON-compatible PromptForge runtime event."""

    timestamp: datetime
    log_type: RuntimeLogType
    level: LogLevel
    message: str
    correlation_id: str
    task_id: str | None = None
    execution_id: str | None = None
    metadata: Mapping[str, JSONValue] = field(
        default_factory=dict,
        hash=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestamp",
            _normalize_timestamp(self.timestamp),
        )
        if self.log_type not in _LOG_TYPES:
            raise ValueError("unsupported runtime log type")
        if self.level not in _LOG_LEVELS:
            raise ValueError("unsupported runtime log level")
        _validate_text(self.message, "message", maximum=16_384)
        _validate_identifier(self.correlation_id, "correlation_id")
        _validate_optional_identifier(self.task_id, "task_id")
        _validate_optional_identifier(self.execution_id, "execution_id")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a detached dictionary using the stable runtime log schema."""

        return {
            "timestamp": _format_timestamp(self.timestamp),
            "log_type": self.log_type,
            "level": self.level,
            "message": self.message,
            "correlation_id": self.correlation_id,
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "metadata": _thaw_json(self.metadata),
        }

    def to_json(self) -> str:
        """Serialize this event as one compact deterministic JSON object."""

        return _json_dumps(self.to_dict())


class RuntimeLogger:
    """Thread-safe structured logger with bounded in-memory retention."""

    def __init__(
        self,
        logger: logging.Logger | None = None,
        *,
        max_logs: int = 10_000,
        clock: Callable[[], datetime] | None = None,
        correlation_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if logger is not None and not isinstance(logger, logging.Logger):
            raise TypeError("logger must be a logging.Logger")
        if not isinstance(max_logs, int) or isinstance(max_logs, bool):
            raise ValueError("max_logs must be a positive integer")
        if max_logs <= 0:
            raise ValueError("max_logs must be a positive integer")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        if correlation_id_factory is not None and not callable(
            correlation_id_factory
        ):
            raise TypeError("correlation_id_factory must be callable")

        self._logger = logger or logging.getLogger("promptforge.runtime")
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._correlation_id_factory = (
            correlation_id_factory
            or (lambda: f"correlation-{uuid.uuid4().hex}")
        )
        self._logs: deque[RuntimeLog] = deque(maxlen=max_logs)
        self._lock = threading.RLock()

    def log_workflow(
        self,
        message: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        *,
        level: LogLevel | int = "INFO",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeLog:
        """Record a workflow lifecycle event."""

        return self._log(
            "workflow",
            message,
            correlation_id=correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            level=level,
            metadata=metadata,
        )

    def log_generation(
        self,
        message: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        *,
        level: LogLevel | int = "INFO",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeLog:
        """Record a source-generation event."""

        return self._log(
            "generation",
            message,
            correlation_id=correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            level=level,
            metadata=metadata,
        )

    def log_build(
        self,
        message: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        *,
        level: LogLevel | int = "INFO",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeLog:
        """Record a firmware build event."""

        return self._log(
            "build",
            message,
            correlation_id=correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            level=level,
            metadata=metadata,
        )

    def log_flash(
        self,
        message: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        *,
        level: LogLevel | int = "INFO",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeLog:
        """Record a firmware flash event."""

        return self._log(
            "flash",
            message,
            correlation_id=correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            level=level,
            metadata=metadata,
        )

    def log_monitor(
        self,
        message: str,
        correlation_id: str | None = None,
        task_id: str | None = None,
        execution_id: str | None = None,
        *,
        level: LogLevel | int = "INFO",
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeLog:
        """Record a serial monitor event."""

        return self._log(
            "monitor",
            message,
            correlation_id=correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            level=level,
            metadata=metadata,
        )

    def export_logs(
        self,
        destination: str | Path | None = None,
        *,
        format: LogExportFormat = "json",
        clear: bool = False,
        indent: int | None = None,
    ) -> str:
        """Export retained logs as JSON or JSON Lines.

        When ``destination`` is supplied, the complete export is atomically
        written as UTF-8.  The serialized content is returned in all cases.
        ``clear=True`` removes exactly the exported snapshot after successful
        serialization and, when applicable, successful file replacement.
        """

        if format not in {"json", "jsonl"}:
            raise ValueError("format must be 'json' or 'jsonl'")
        if indent is not None and (
            not isinstance(indent, int)
            or isinstance(indent, bool)
            or indent < 0
        ):
            raise ValueError("indent must be a non-negative integer or None")

        with self._lock:
            snapshot = tuple(self._logs)
            serialized = _serialize_logs(snapshot, format=format, indent=indent)
            if destination is not None:
                _atomic_write(Path(destination), serialized)
            if clear:
                self._remove_snapshot(snapshot)
            return serialized

    @property
    def log_count(self) -> int:
        """Return the number of currently retained runtime logs."""

        with self._lock:
            return len(self._logs)

    def _log(
        self,
        log_type: RuntimeLogType,
        message: str,
        *,
        correlation_id: str | None,
        task_id: str | None,
        execution_id: str | None,
        level: LogLevel | int,
        metadata: Mapping[str, Any] | None,
    ) -> RuntimeLog:
        normalized_level = _normalize_level(level)
        selected_correlation_id = (
            correlation_id
            if correlation_id is not None
            else self._correlation_id_factory()
        )
        runtime_log = RuntimeLog(
            timestamp=self._clock(),
            log_type=log_type,
            level=normalized_level,
            message=message,
            correlation_id=selected_correlation_id,
            task_id=task_id,
            execution_id=execution_id,
            metadata={} if metadata is None else metadata,
        )

        with self._lock:
            self._logs.append(runtime_log)

        try:
            self._logger.log(
                _LOG_LEVELS[normalized_level],
                runtime_log.to_json(),
                extra={
                    "runtime_log_type": runtime_log.log_type,
                    "correlation_id": runtime_log.correlation_id,
                    "task_id": runtime_log.task_id,
                    "execution_id": runtime_log.execution_id,
                },
            )
        except Exception:
            # Runtime telemetry must not interrupt the workflow it observes.
            pass
        return runtime_log

    def _remove_snapshot(self, snapshot: Sequence[RuntimeLog]) -> None:
        for expected in snapshot:
            if not self._logs or self._logs[0] is not expected:
                raise RuntimeError("runtime log history changed during export")
            self._logs.popleft()


def _normalize_level(level: LogLevel | int) -> LogLevel:
    if isinstance(level, bool):
        raise ValueError("level must be a supported logging level")
    if isinstance(level, int):
        for name, value in _LOG_LEVELS.items():
            if level == value:
                return name  # type: ignore[return-value]
        raise ValueError("level must be a supported logging level")
    if not isinstance(level, str):
        raise TypeError("level must be a string or integer")
    normalized = level.strip().upper()
    if normalized == "WARN":
        normalized = "WARNING"
    if normalized not in _LOG_LEVELS:
        raise ValueError("level must be a supported logging level")
    return normalized  # type: ignore[return-value]


def _normalize_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_text(value: object, field_name: str, *, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _validate_identifier(value: object, field_name: str) -> None:
    _validate_text(value, field_name, maximum=256)


def _validate_optional_identifier(value: object, field_name: str) -> None:
    if value is not None:
        _validate_identifier(value, field_name)


def _freeze_metadata(value: object) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    frozen = _freeze_json(value, path="metadata", active=set())
    if not isinstance(frozen, Mapping):
        raise ValueError("metadata must be a mapping")
    return frozen


def _freeze_json(value: object, *, path: str, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value

    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} cannot contain cyclic references")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            result: dict[str, JSONValue] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(
                        f"{path} keys must be non-empty strings"
                    )
                result[key] = _freeze_json(
                    item,
                    path=f"{path}.{key}",
                    active=active,
                )
            return MappingProxyType(result)
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return tuple(
                _freeze_json(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            )
    finally:
        active.remove(identity)
    raise ValueError(f"{path} contains a non-JSON-compatible value")


def _thaw_json(value: object) -> JSONValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value  # type: ignore[return-value]


def _serialize_logs(
    logs: Sequence[RuntimeLog],
    *,
    format: LogExportFormat,
    indent: int | None,
) -> str:
    if format == "jsonl":
        return "\n".join(item.to_json() for item in logs)
    return json.dumps(
        [item.to_dict() for item in logs],
        ensure_ascii=True,
        allow_nan=False,
        indent=indent,
        sort_keys=True,
        separators=None if indent is not None else (",", ":"),
    )


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _atomic_write(destination: Path, content: str) -> None:
    path = destination.expanduser()
    if path.exists() and path.is_dir():
        raise IsADirectoryError(f"log destination is a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        file = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = None
        with file:
            file.write(content)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
