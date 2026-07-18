"""Versioned JSONL persistence for experimental unified coding workflows."""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any


CODING_WORKFLOW_RUN_SCHEMA_VERSION = "forgex.coding_workflow_run.v1"
CODING_WORKFLOW_EVENT_SCHEMA_VERSION = "forgex.coding_workflow_event.v1"
RUNS_FILENAME = "coding-workflow-runs.jsonl"
EVENTS_FILENAME = "coding-workflow-events.jsonl"

MAX_MESSAGE_CHARS = 2_000
MAX_FILES_CHANGED = 20
MAX_METADATA_BYTES = 16 * 1024
MAX_METADATA_STRING_CHARS = 2_000

RUN_ALREADY_EXISTS = "CODING_WORKFLOW_RUN_ALREADY_EXISTS"
RUN_NOT_FOUND = "CODING_WORKFLOW_RUN_NOT_FOUND"
RUN_STATUS_INVALID = "CODING_WORKFLOW_RUN_STATUS_INVALID"
OPERATION_IN_PROGRESS = "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
INVALID_STATE_TRANSITION = "CODING_WORKFLOW_INVALID_STATE_TRANSITION"
EVENT_SEQUENCE_INVALID = "CODING_WORKFLOW_EVENT_SEQUENCE_INVALID"
STORE_CORRUPT = "CODING_WORKFLOW_STORE_CORRUPT"
RECORD_INVALID = "CODING_WORKFLOW_RECORD_INVALID"
LEGACY_WORKFLOW_ADMISSION_DISABLED = "LEGACY_WORKFLOW_ADMISSION_DISABLED"
LEGACY_WORKFLOW_READ_ONLY = "LEGACY_WORKFLOW_READ_ONLY"
STALE_RECOVERY_CODE = "CODING_WORKFLOW_RECOVERED_FROM_STALE_STATE"

IN_PROGRESS_STATUSES = frozenset({
    "applying",
    "building",
    "flashing",
    "monitoring",
    "repairing",
    "cancelling",
})
IN_PROGRESS_STAGE_BY_STATUS = MappingProxyType({
    "applying": "apply",
    "building": "build",
    "flashing": "flash",
    "monitoring": "monitor",
    "repairing": "repair",
    "cancelling": "cancel",
})
CANCELLABLE_STATUSES = frozenset({
    "awaiting_apply",
    "awaiting_build",
    "awaiting_flash",
    "awaiting_monitor",
    "failed",
})
VALID_TRANSITIONS = MappingProxyType({
    "awaiting_apply": frozenset({"applying", "cancelling", "failed"}),
    "applying": frozenset({"awaiting_build", "failed"}),
    "awaiting_build": frozenset({"building", "cancelling", "failed"}),
    "building": frozenset({"awaiting_flash", "failed"}),
    "awaiting_flash": frozenset({"flashing", "cancelling", "failed"}),
    "flashing": frozenset({"awaiting_monitor", "failed"}),
    "awaiting_monitor": frozenset({"monitoring", "cancelling", "failed"}),
    "monitoring": frozenset({"completed", "failed"}),
    "failed": frozenset({"repairing", "cancelling"}),
    "repairing": frozenset({"awaiting_apply", "failed"}),
    "cancelling": frozenset({"cancelled", "failed"}),
})

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_FORBIDDEN_KEY_MARKERS = (
    "api_key", "apikey", "credential", "password", "secret", "token",
    "raw_prompt", "raw_response", "source_body", "source_code", "content",
    "env_contents", "workspace_path", "workspace_root",
)
_SECRET_VALUE_MARKERS = (
    "-----begin private key-----",
    "-----begin rsa private key-----",
    "openai_api_key=",
    "password=",
    "secret=",
    "token=",
)
_RUN_FIELDS = frozenset({
    "schema_version", "run_id", "task_id", "project_id", "provider_id", "provider_type",
    "status", "generation_status", "review_id", "next_action", "files_changed", "created_at",
    "updated_at", "safe_summary", "failure_code", "safe_message", "metadata",
})
_EVENT_FIELDS = frozenset({
    "schema_version", "event_id", "run_id", "sequence", "event_type", "stage", "status",
    "safe_message", "created_at", "metadata",
})
_UNSET = object()


class CodingWorkflowStoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CodingWorkflowRunRecord:
    run_id: str
    provider_id: str
    provider_type: str
    status: str
    schema_version: str = CODING_WORKFLOW_RUN_SCHEMA_VERSION
    task_id: str | None = None
    project_id: str | None = None
    generation_status: str | None = None
    review_id: str | None = None
    next_action: str | None = None
    files_changed: tuple[str, ...] = ()
    created_at: str = field(default_factory=lambda: _utc_now())
    updated_at: str = field(default_factory=lambda: _utc_now())
    safe_summary: str | None = None
    failure_code: str | None = None
    safe_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != CODING_WORKFLOW_RUN_SCHEMA_VERSION:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Unsupported coding workflow run schema version.")
        for name in ("run_id", "provider_id", "provider_type", "status"):
            _required_text(getattr(self, name), name, 128)
        _identifier(self.run_id, "run_id")
        for name in ("task_id", "project_id", "review_id"):
            value = getattr(self, name)
            if value is not None:
                _identifier(value, name)
        for name in ("generation_status", "next_action", "failure_code"):
            _optional_text(getattr(self, name), name, 128)
        _optional_text(self.safe_summary, "safe_summary", MAX_MESSAGE_CHARS)
        _optional_text(self.safe_message, "safe_message", MAX_MESSAGE_CHARS)
        _reject_secret_text(self.safe_summary, "safe_summary")
        _reject_secret_text(self.safe_message, "safe_message")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.updated_at, "updated_at")
        files = tuple(self.files_changed)
        if len(files) > MAX_FILES_CHANGED or any(not _safe_relative_path(path) for path in files):
            raise CodingWorkflowStoreError(RECORD_INVALID, "files_changed is invalid.")
        if len({path.casefold() for path in files}) != len(files):
            raise CodingWorkflowStoreError(RECORD_INVALID, "files_changed contains duplicates.")
        if self.status not in {"queued", "validating", "preparing_sandbox", "running", "awaiting_review", "awaiting_apply", "applying", "awaiting_build", "building", "awaiting_flash", "flashing", "awaiting_monitor", "monitoring", "repairing", "cancelling", "completed", "failed", "cancelled", "timed_out"}:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Run status is invalid.")
        if self.status == "awaiting_apply" and (
            self.generation_status not in {"review_created", "repair_review_created"}
            or not self.review_id
            or self.next_action != "await_user_approval"
            or self.failure_code is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Awaiting-apply run linkage is invalid.")
        if self.status == "failed" and (self.generation_status != "failed" or not self.failure_code or not self.safe_message):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Failed run linkage is invalid.")
        if self.status == "applying" and (
            self.generation_status != "review_created" or not self.review_id or self.next_action is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Applying run linkage is invalid.")
        if self.status == "awaiting_build" and (
            self.generation_status != "review_created"
            or not self.review_id
            or self.next_action != "run_build"
            or self.failure_code is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Awaiting-build run linkage is invalid.")
        if self.status == "building" and (
            self.generation_status != "review_created" or not self.review_id or self.next_action is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Building run linkage is invalid.")
        if self.status == "awaiting_flash" and (
            self.generation_status != "review_created"
            or not self.review_id
            or self.next_action != "confirm_flash"
            or self.failure_code is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Awaiting-flash run linkage is invalid.")
        if self.status == "flashing" and (
            self.generation_status != "review_created" or not self.review_id or self.next_action is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Flashing run linkage is invalid.")
        if self.status == "awaiting_monitor" and (
            self.generation_status != "review_created"
            or not self.review_id
            or self.next_action != "open_monitor"
            or self.failure_code is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Awaiting-monitor run linkage is invalid.")
        if self.status == "monitoring" and (
            self.generation_status != "review_created" or not self.review_id or self.next_action is not None
        ):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Monitoring run linkage is invalid.")
        if self.status == "repairing" and self.next_action is not None:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Repairing run linkage is invalid.")
        if self.status == "cancelling" and self.next_action is not None:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Cancelling run linkage is invalid.")
        if self.status == "cancelled" and (self.next_action is not None or self.failure_code is not None):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Cancelled run linkage is invalid.")
        object.__setattr__(self, "files_changed", files)
        object.__setattr__(self, "metadata", _safe_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "status": self.status,
            "generation_status": self.generation_status,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "files_changed": list(self.files_changed),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "safe_summary": self.safe_summary,
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
            "metadata": _thaw(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CodingWorkflowRunRecord":
        row = _strict_row(value, _RUN_FIELDS, "run")
        try:
            return cls(
                schema_version=row["schema_version"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                project_id=row["project_id"],
                provider_id=row["provider_id"],
                provider_type=row["provider_type"],
                status=row["status"],
                generation_status=row["generation_status"],
                review_id=row["review_id"],
                next_action=row["next_action"],
                files_changed=tuple(row["files_changed"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                safe_summary=row["safe_summary"],
                failure_code=row["failure_code"],
                safe_message=row["safe_message"],
                metadata=row["metadata"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, CodingWorkflowStoreError):
                raise
            raise CodingWorkflowStoreError(RECORD_INVALID, "Coding workflow run record is invalid.") from exc


@dataclass(frozen=True, slots=True)
class CodingWorkflowEventRecord:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    stage: str
    status: str
    safe_message: str
    schema_version: str = CODING_WORKFLOW_EVENT_SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: _utc_now())
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != CODING_WORKFLOW_EVENT_SCHEMA_VERSION:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Unsupported coding workflow event schema version.")
        for name in ("event_id", "run_id"):
            _identifier(getattr(self, name), name)
        for name in ("event_type", "stage", "status"):
            _required_text(getattr(self, name), name, 128)
        _required_text(self.safe_message, "safe_message", MAX_MESSAGE_CHARS)
        _reject_secret_text(self.safe_message, "safe_message")
        _timestamp(self.created_at, "created_at")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Event sequence must be a positive integer.")
        object.__setattr__(self, "metadata", _safe_metadata(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "stage": self.stage,
            "status": self.status,
            "safe_message": self.safe_message,
            "created_at": self.created_at,
            "metadata": _thaw(self.metadata),
        }

    @classmethod
    def from_dict(cls, value: object) -> "CodingWorkflowEventRecord":
        row = _strict_row(value, _EVENT_FIELDS, "event")
        try:
            return cls(
                schema_version=row["schema_version"],
                event_id=row["event_id"],
                run_id=row["run_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                stage=row["stage"],
                status=row["status"],
                safe_message=row["safe_message"],
                created_at=row["created_at"],
                metadata=row["metadata"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, CodingWorkflowStoreError):
                raise
            raise CodingWorkflowStoreError(RECORD_INVALID, "Coding workflow event record is invalid.") from exc


class CodingWorkflowStore:
    def __init__(self, *, runs_path: str | Path, events_path: str | Path, allow_new_runs: bool = True, allow_mutations: bool = True) -> None:
        self.runs_path = Path(runs_path)
        self.events_path = Path(events_path)
        self._allow_new_runs = bool(allow_new_runs)
        self._allow_mutations = bool(allow_mutations)
        self._lock = threading.RLock()
        self._operation_locks: dict[str, str] = {}

    @property
    def allow_new_runs(self) -> bool:
        return self._allow_new_runs

    @property
    def allow_mutations(self) -> bool:
        return self._allow_mutations

    @classmethod
    def from_state_directory(cls, state_directory: str | Path, *, allow_new_runs: bool = True, allow_mutations: bool = True) -> "CodingWorkflowStore":
        root = Path(state_directory)
        return cls(runs_path=root / RUNS_FILENAME, events_path=root / EVENTS_FILENAME, allow_new_runs=allow_new_runs, allow_mutations=allow_mutations)

    def _require_mutations(self) -> None:
        if not self._allow_mutations:
            raise CodingWorkflowStoreError(
                LEGACY_WORKFLOW_READ_ONLY,
                "Historical legacy workflows are read-only; use the durable workflow service.",
            )

    def create_run(self, record: CodingWorkflowRunRecord) -> None:
        if not self._allow_new_runs:
            raise CodingWorkflowStoreError(
                LEGACY_WORKFLOW_ADMISSION_DISABLED,
                "New legacy workflow admission is disabled; use the durable workflow service.",
            )
        self._require_mutations()
        if not isinstance(record, CodingWorkflowRunRecord):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Run record type is invalid.")
        with self._lock:
            if any(item.run_id == record.run_id for item in self._load_run_versions()):
                raise CodingWorkflowStoreError(RUN_ALREADY_EXISTS, "Coding workflow run already exists.")
            _append_jsonl(self.runs_path, record.to_dict())

    def append_event(self, event: CodingWorkflowEventRecord) -> None:
        self._require_mutations()
        if not isinstance(event, CodingWorkflowEventRecord):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Event record type is invalid.")
        with self._lock:
            self.get_run(event.run_id)
            events = self._load_events()
            if any(item.event_id == event.event_id for item in events):
                raise CodingWorkflowStoreError(EVENT_SEQUENCE_INVALID, "Coding workflow event ID already exists.")
            existing = [item for item in events if item.run_id == event.run_id]
            expected = existing[-1].sequence + 1 if existing else 1
            if event.sequence != expected:
                raise CodingWorkflowStoreError(EVENT_SEQUENCE_INVALID, "Coding workflow event sequence is not monotonic.")
            _append_jsonl(self.events_path, event.to_dict())

    def persist_run(
        self,
        record: CodingWorkflowRunRecord,
        events: Sequence[CodingWorkflowEventRecord],
    ) -> None:
        event_values = tuple(events)
        if any(not isinstance(event, CodingWorkflowEventRecord) or event.run_id != record.run_id for event in event_values):
            raise CodingWorkflowStoreError(RECORD_INVALID, "Persisted events must belong to the run.")
        if tuple(event.sequence for event in event_values) != tuple(range(1, len(event_values) + 1)):
            raise CodingWorkflowStoreError(EVENT_SEQUENCE_INVALID, "Persisted event sequence is not monotonic.")
        if len({event.event_id for event in event_values}) != len(event_values):
            raise CodingWorkflowStoreError(EVENT_SEQUENCE_INVALID, "Persisted event IDs must be unique.")
        with self._lock:
            self.create_run(record)
            for event in event_values:
                self.append_event(event)

    def get_run(self, run_id: str) -> CodingWorkflowRunRecord:
        _identifier(run_id, "run_id")
        with self._lock:
            matches = [item for item in self._load_run_versions() if item.run_id == run_id]
            if not matches:
                raise CodingWorkflowStoreError(RUN_NOT_FOUND, "Coding workflow run was not found.")
            return matches[-1]

    def list_runs(self, *, limit: int = 100) -> tuple[CodingWorkflowRunRecord, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 1_000:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Run list limit is invalid.")
        with self._lock:
            latest: dict[str, CodingWorkflowRunRecord] = {}
            for record in self._load_run_versions():
                latest[record.run_id] = record
            ordered = sorted(latest.values(), key=lambda item: (item.updated_at, item.run_id), reverse=True)
            return tuple(ordered[:limit])

    def list_events(self, run_id: str) -> tuple[CodingWorkflowEventRecord, ...]:
        _identifier(run_id, "run_id")
        with self._lock:
            events = tuple(item for item in self._load_events() if item.run_id == run_id)
            if tuple(item.sequence for item in events) != tuple(range(1, len(events) + 1)):
                raise CodingWorkflowStoreError(STORE_CORRUPT, "Stored coding workflow event sequence is corrupt.")
            return events

    def acquire_operation_lock(self, run_id: str, operation: str) -> None:
        self._require_mutations()
        _identifier(run_id, "run_id")
        _required_text(operation, "operation", 64)
        with self._lock:
            self.get_run(run_id)
            if run_id in self._operation_locks:
                raise CodingWorkflowStoreError(OPERATION_IN_PROGRESS, "Coding workflow operation is already in progress.")
            self._operation_locks[run_id] = operation

    def release_operation_lock(self, run_id: str, operation: str | None = None) -> None:
        _identifier(run_id, "run_id")
        with self._lock:
            if operation is None or self._operation_locks.get(run_id) == operation:
                self._operation_locks.pop(run_id, None)

    def is_operation_locked(self, run_id: str) -> bool:
        _identifier(run_id, "run_id")
        with self._lock:
            return run_id in self._operation_locks

    def mark_run_completed_or_failed(
        self,
        run_id: str,
        *,
        status: str,
        generation_status: str,
        review_id: str | None = None,
        next_action: str | None = None,
        files_changed: tuple[str, ...] = (),
        safe_summary: str | None = None,
        failure_code: str | None = None,
        safe_message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CodingWorkflowRunRecord:
        self._require_mutations()
        with self._lock:
            current = self.get_run(run_id)
            updated = replace(
                current,
                status=status,
                generation_status=generation_status,
                review_id=review_id,
                next_action=next_action,
                files_changed=files_changed,
                safe_summary=safe_summary,
                failure_code=failure_code,
                safe_message=safe_message,
                metadata=current.metadata if metadata is None else metadata,
                updated_at=_utc_now(),
            )
            _append_jsonl(self.runs_path, updated.to_dict())
            return updated

    def transition_run(
        self,
        run_id: str,
        *,
        expected_statuses: Sequence[str],
        status: str,
        generation_status: str,
        next_action: str | None,
        review_id: str | None | object = _UNSET,
        files_changed: tuple[str, ...] | object = _UNSET,
        safe_summary: str | None | object = _UNSET,
        failure_code: str | None = None,
        safe_message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> CodingWorkflowRunRecord:
        """Append a validated run version after checking its current state."""

        self._require_mutations()
        with self._lock:
            current = self.get_run(run_id)
            if current.status not in frozenset(expected_statuses):
                raise CodingWorkflowStoreError(RUN_STATUS_INVALID, "Coding workflow run status changed.")
            _validate_transition(current.status, status)
            updated = replace(
                current,
                status=status,
                generation_status=generation_status,
                review_id=current.review_id if review_id is _UNSET else review_id,  # type: ignore[arg-type]
                next_action=next_action,
                files_changed=current.files_changed if files_changed is _UNSET else files_changed,  # type: ignore[arg-type]
                safe_summary=current.safe_summary if safe_summary is _UNSET else safe_summary,  # type: ignore[arg-type]
                failure_code=failure_code,
                safe_message=safe_message,
                metadata=current.metadata if metadata is None else metadata,
                updated_at=_utc_now(),
            )
            _append_jsonl(self.runs_path, updated.to_dict())
            return updated

    def detect_stale_in_progress_runs(
        self,
        *,
        threshold_seconds: int = 30 * 60,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[dict[str, object], ...]:
        if not isinstance(threshold_seconds, int) or isinstance(threshold_seconds, bool) or threshold_seconds < 1:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Stale threshold is invalid.")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 1_000:
            raise CodingWorkflowStoreError(RECORD_INVALID, "Stale result limit is invalid.")
        checked_at = now or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            checked_at = checked_at.replace(tzinfo=timezone.utc)
        stale: list[dict[str, object]] = []
        with self._lock:
            for run in self.list_runs(limit=1000):
                if run.status not in IN_PROGRESS_STATUSES:
                    continue
                started_at = self._in_progress_started_at(run)
                age_seconds = max(0, int((checked_at - _parse_timestamp(started_at)).total_seconds()))
                if age_seconds < threshold_seconds:
                    continue
                stage = IN_PROGRESS_STAGE_BY_STATUS.get(run.status, run.status)
                stale.append({
                    "run_id": run.run_id,
                    "status": run.status,
                    "stage": stage,
                    "started_at": started_at,
                    "age_seconds": age_seconds,
                    "suggested_action": "manual_mark_failed",
                })
                if len(stale) >= limit:
                    break
        return tuple(stale)

    def is_stale_in_progress(
        self,
        run_id: str,
        *,
        threshold_seconds: int = 30 * 60,
        now: datetime | None = None,
    ) -> bool:
        _identifier(run_id, "run_id")
        return any(item["run_id"] == run_id for item in self.detect_stale_in_progress_runs(threshold_seconds=threshold_seconds, now=now, limit=1000))

    def _in_progress_started_at(self, run: CodingWorkflowRunRecord) -> str:
        stage = IN_PROGRESS_STAGE_BY_STATUS.get(run.status)
        expected_event = f"{stage}.started" if stage else None
        events = self.list_events(run.run_id)
        if expected_event is not None:
            for event in reversed(events):
                if event.event_type == expected_event:
                    return event.created_at
        return run.updated_at

    def _load_run_versions(self) -> tuple[CodingWorkflowRunRecord, ...]:
        return tuple(CodingWorkflowRunRecord.from_dict(row) for row in _read_jsonl(self.runs_path))

    def _load_events(self) -> tuple[CodingWorkflowEventRecord, ...]:
        return tuple(CodingWorkflowEventRecord.from_dict(row) for row in _read_jsonl(self.events_path))


def _append_jsonl(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise CodingWorkflowStoreError("CODING_WORKFLOW_PERSISTENCE_FAILED", "Coding workflow record could not be persisted.") from exc
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    except OSError as exc:
        raise CodingWorkflowStoreError("CODING_WORKFLOW_PERSISTENCE_FAILED", "Coding workflow record could not be persisted.") from exc
    finally:
        os.close(descriptor)


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CodingWorkflowStoreError(STORE_CORRUPT, "Coding workflow store could not be read safely.") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodingWorkflowStoreError(STORE_CORRUPT, f"Coding workflow store is malformed at line {line_number}.") from exc
        if not isinstance(value, dict):
            raise CodingWorkflowStoreError(STORE_CORRUPT, f"Coding workflow store record is invalid at line {line_number}.")
        rows.append(value)
    return tuple(rows)


def _strict_row(value: object, fields: frozenset[str], kind: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise CodingWorkflowStoreError(RECORD_INVALID, f"Coding workflow {kind} record schema is invalid.")
    return value


def _safe_metadata(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CodingWorkflowStoreError(RECORD_INVALID, "Metadata must be a mapping.")
    frozen = _freeze_mapping(value, "metadata", set(), 0)
    encoded = json.dumps(_thaw(frozen), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_METADATA_BYTES:
        raise CodingWorkflowStoreError(RECORD_INVALID, "Metadata exceeds the persistence limit.")
    return frozen


def _freeze_mapping(value: Mapping[object, Any], path: str, active: set[int], depth: int) -> Mapping[str, Any]:
    if depth > 8 or id(value) in active:
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} is too deeply nested or cyclic.")
    active.add(id(value))
    try:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or any(marker in key.casefold() for marker in _FORBIDDEN_KEY_MARKERS):
                raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} contains an unsafe metadata key.")
            result[key] = _freeze_value(item, f"{path}.{key}", active, depth + 1)
        return MappingProxyType(result)
    finally:
        active.remove(id(value))


def _freeze_value(value: Any, path: str, active: set[int], depth: int) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} must be finite.")
    if isinstance(value, str):
        if len(value) > MAX_METADATA_STRING_CHARS or "\x00" in value:
            raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} exceeds the safe string limit.")
        folded = value.casefold().replace(" ", "")
        if any(marker.replace(" ", "") in folded for marker in _SECRET_VALUE_MARKERS):
            raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} appears to contain secret material.")
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value, path, active, depth)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if depth > 8 or id(value) in active:
            raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} is too deeply nested or cyclic.")
        active.add(id(value))
        try:
            return tuple(_freeze_value(item, f"{path}[{index}]", active, depth + 1) for index, item in enumerate(value))
        finally:
            active.remove(id(value))
    raise CodingWorkflowStoreError(RECORD_INVALID, f"{path} is not JSON-compatible.")


def _required_text(value: object, field_name: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > max_chars:
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{field_name} is invalid.")
    return value


def _reject_secret_text(value: object, field_name: str) -> None:
    if value is None or not isinstance(value, str):
        return
    folded = value.casefold().replace(" ", "")
    if any(marker.replace(" ", "") in folded for marker in _SECRET_VALUE_MARKERS):
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{field_name} appears to contain secret material.")


def _optional_text(value: object, field_name: str, max_chars: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name, max_chars)


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{field_name} is invalid.")
    return value


def _timestamp(value: object, field_name: str) -> str:
    text = _required_text(value, field_name, 64)
    try:
        parsed = _parse_timestamp(text)
    except ValueError as exc:
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{field_name} is invalid.") from exc
    if parsed.tzinfo is None:
        raise CodingWorkflowStoreError(RECORD_INVALID, f"{field_name} must be timezone-aware.")
    return text


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_transition(current_status: str, next_status: str) -> None:
    # The caller has already checked ``expected_statuses``. Same-state writes
    # are versioned metadata updates, not state-machine transitions.
    if current_status == next_status:
        return
    allowed = VALID_TRANSITIONS.get(current_status)
    if allowed is None:
        return
    if next_status not in allowed:
        raise CodingWorkflowStoreError(INVALID_STATE_TRANSITION, "Coding workflow state transition is invalid.")


def _safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    if value.startswith("/") or PurePosixPath(value).is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
