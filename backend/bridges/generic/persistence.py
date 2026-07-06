"""Atomic metadata persistence for generic bridge runs, events, and references."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from .errors import BridgeDomainError, BridgeErrorCode
from .models import BridgeArtifactReference, BridgeArtifactType, BridgeEventType, BridgeRunEvent, BridgeRunRequest
from .states import BRIDGE_RUN_SCHEMA_VERSION, BridgeRunRecord, BridgeRunStateMachine, BridgeRunStatus
from .validation import parse_datetime, utc_now


STORE_SCHEMA_VERSION = 1
DEFAULT_MAX_RECORDS = 500


class BridgeRunStore:
    """Single metadata store; artifact bytes remain owned by review/patch services."""

    def __init__(self, path: str | Path, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self.path = Path(path)
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self.max_records = max_records
        self._lock = threading.RLock()

    def create(self, request: BridgeRunRequest) -> BridgeRunRecord:
        with self._lock:
            if any(item.run_id == request.run_id for item in self.list_records()):
                raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Bridge run already exists.")
            record = BridgeRunRecord(
                run_id=request.run_id,
                provider_id=request.provider_id,
                status=BridgeRunStatus.QUEUED,
                created_at=request.created_at,
                updated_at=request.created_at,
                project_id=request.project_id,
                sandbox_id=request.sandbox_id,
                correlation_id=request.correlation_id,
                instruction_hash=request.instruction_hash,
                instruction_length=request.instruction_length,
                execution_mode=request.execution_mode,
            )
            self.upsert_record(record)
            return record

    def upsert_record(self, record: BridgeRunRecord, *, expected_revision: int | None = None) -> BridgeRunRecord:
        with self._lock:
            payload = self._read_payload()
            records = {item.run_id: item for item in self._records_from_payload(payload)}
            existing = records.get(record.run_id)
            if existing is not None and record.updated_at < existing.updated_at:
                raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED, "Run history cannot move backwards.")
            if expected_revision is not None:
                if existing is None or existing.revision != expected_revision:
                    raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED, "Bridge run update is stale.")
            stored = replace(record, revision=(existing.revision + 1) if existing is not None else record.revision)
            records[record.run_id] = stored
            retained = sorted(records.values(), key=lambda item: (item.created_at, item.run_id))[-self.max_records :]
            retained_ids = {item.run_id for item in retained}
            payload["records"] = [item.to_dict() for item in retained]
            payload["events"] = [row for row in payload["events"] if row.get("run_id") in retained_ids]
            payload["artifacts"] = [row for row in payload["artifacts"] if row.get("run_id") in retained_ids]
            self._write_payload(payload)
            return stored

    def get_record(self, run_id: str) -> BridgeRunRecord:
        with self._lock:
            for record in self.list_records():
                if record.run_id == run_id:
                    return record
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Bridge run was not found.")

    def list_records(self) -> tuple[BridgeRunRecord, ...]:
        with self._lock:
            return tuple(self._records_from_payload(self._read_payload()))

    def append_event(self, event: BridgeRunEvent) -> None:
        with self._lock:
            self._append_event_locked(event)

    def _append_event_locked(self, event: BridgeRunEvent) -> None:
        payload = self._read_payload()
        records = {item.run_id: item for item in self._records_from_payload(payload)}
        record = records.get(event.run_id)
        if record is None:
            raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED, "Event run was not found.")
        existing = [event_from_dict(row) for row in payload["events"] if row.get("run_id") == event.run_id]
        if [item.sequence for item in sorted(existing, key=lambda item: item.sequence)] != list(range(1, len(existing) + 1)):
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        expected = len(existing) + 1
        if event.sequence != expected or event.sequence != record.event_count + 1:
            raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED, "Event sequence is not monotonic.")
        payload["events"].append(event.to_dict())
        records[event.run_id] = replace(record, event_count=event.sequence, revision=record.revision + 1)
        payload["records"] = [item.to_dict() for item in sorted(records.values(), key=lambda item: (item.created_at, item.run_id))]
        self._write_payload(payload)

    def list_events(self, run_id: str) -> tuple[BridgeRunEvent, ...]:
        with self._lock:
            payload = self._read_payload()
            events = [event_from_dict(row) for row in payload["events"] if row.get("run_id") == run_id]
            ordered = tuple(sorted(events, key=lambda item: item.sequence))
            if [item.sequence for item in ordered] != list(range(1, len(ordered) + 1)):
                raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
            return ordered

    def add_artifact(self, artifact: BridgeArtifactReference, *, managed_root: Path) -> None:
        with self._lock:
            artifact.resolve_internal(managed_root)
            self._add_artifact_locked(artifact)

    def _add_artifact_locked(self, artifact: BridgeArtifactReference) -> None:
        payload = self._read_payload()
        records = {item.run_id: item for item in self._records_from_payload(payload)}
        record = records.get(artifact.run_id)
        if record is None:
            raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED, "Artifact run was not found.")
        if any(row.get("artifact_id") == artifact.artifact_id for row in payload["artifacts"]):
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID, "Artifact identifier already exists.")
        payload["artifacts"].append(artifact.to_storage_dict())
        records[artifact.run_id] = replace(
            record,
            artifact_count=record.artifact_count + 1,
            revision=record.revision + 1,
        )
        payload["records"] = [item.to_dict() for item in sorted(records.values(), key=lambda item: (item.created_at, item.run_id))]
        self._write_payload(payload)

    def list_artifacts(self, run_id: str) -> tuple[BridgeArtifactReference, ...]:
        with self._lock:
            payload = self._read_payload()
            return tuple(artifact_from_dict(row) for row in payload["artifacts"] if row.get("run_id") == run_id)

    def reconcile_incomplete_runs(self, *, at=None) -> tuple[BridgeRunRecord, ...]:
        with self._lock:
            payload = self._read_payload()
            machine = BridgeRunStateMachine()
            timestamp = at or utc_now()
            reconciled = []
            for record in self._records_from_payload(payload):
                updated = machine.reconcile_after_restart(record, at=max(timestamp, record.updated_at))
                if updated is not record:
                    updated = replace(updated, revision=record.revision + 1)
                reconciled.append(updated)
            payload["records"] = [item.to_dict() for item in reconciled]
            self._write_payload(payload)
            return tuple(reconciled)

    def _empty_payload(self) -> dict[str, Any]:
        return {"schema_version": STORE_SCHEMA_VERSION, "records": [], "events": [], "artifacts": []}

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty_payload()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != STORE_SCHEMA_VERSION:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        for field in ("records", "events", "artifacts"):
            if not isinstance(payload.get(field), list):
                raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
            if any(not isinstance(row, dict) for row in payload[field]):
                raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        return payload

    def _records_from_payload(self, payload: dict[str, Any]) -> list[BridgeRunRecord]:
        try:
            records = [record_from_dict(row) for row in payload["records"]]
        except (KeyError, TypeError, ValueError, BridgeDomainError) as exc:
            if isinstance(exc, BridgeDomainError) and exc.code is BridgeErrorCode.RECORD_CORRUPT:
                raise
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc
        return sorted(records, key=lambda item: (item.created_at, item.run_id))

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.tmp")
        data = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            raise BridgeDomainError(BridgeErrorCode.PERSISTENCE_FAILED) from exc


def record_from_dict(data: dict[str, Any]) -> BridgeRunRecord:
    def optional_datetime(name: str):
        return None if data.get(name) is None else parse_datetime(data[name], name)

    if int(data.get("schema_version", -1)) != BRIDGE_RUN_SCHEMA_VERSION:
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
    try:
        return BridgeRunRecord(
            schema_version=int(data["schema_version"]),
            run_id=str(data["run_id"]),
            provider_id=str(data["provider_id"]),
            status=BridgeRunStatus(str(data["status"])),
            created_at=parse_datetime(data["created_at"], "created_at"),
            started_at=optional_datetime("started_at"),
            finished_at=optional_datetime("finished_at"),
            updated_at=parse_datetime(data["updated_at"], "updated_at"),
            project_id=str(data["project_id"]),
            sandbox_id=str(data["sandbox_id"]),
            correlation_id=str(data["correlation_id"]),
            instruction_hash=str(data["instruction_hash"]),
            instruction_length=int(data["instruction_length"]),
            execution_mode=str(data.get("execution_mode", "generic")),
            revision=int(data.get("revision", 0)),
            event_count=int(data.get("event_count", 0)),
            artifact_count=int(data.get("artifact_count", 0)),
            failure_code=None if data.get("failure_code") is None else str(data["failure_code"]),
            safe_failure_message=None if data.get("safe_failure_message") is None else str(data["safe_failure_message"]),
            cancellation_requested=bool(data.get("cancellation_requested", False)),
            cancellation_requested_at=optional_datetime("cancellation_requested_at"),
            cancellation_reason_code=None if data.get("cancellation_reason_code") is None else str(data["cancellation_reason_code"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc


def event_from_dict(data: dict[str, Any]) -> BridgeRunEvent:
    try:
        return BridgeRunEvent(
            event_id=str(data["event_id"]),
            run_id=str(data["run_id"]),
            sequence=int(data["sequence"]),
            timestamp=parse_datetime(data["timestamp"], "timestamp"),
            event_type=BridgeEventType(str(data["event_type"])),
            status=None if data.get("status") is None else str(data["status"]),
            safe_message=None if data.get("safe_message") is None else str(data["safe_message"]),
            progress=None if data.get("progress") is None else int(data["progress"]),
            failure_code=None if data.get("failure_code") is None else str(data["failure_code"]),
            artifact_id=None if data.get("artifact_id") is None else str(data["artifact_id"]),
        )
    except (KeyError, TypeError, ValueError, BridgeDomainError) as exc:
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc


def artifact_from_dict(data: dict[str, Any]) -> BridgeArtifactReference:
    try:
        return BridgeArtifactReference(
            artifact_id=str(data["artifact_id"]),
            run_id=str(data["run_id"]),
            artifact_type=BridgeArtifactType(str(data["artifact_type"])),
            created_at=parse_datetime(data["created_at"], "created_at"),
            content_hash=str(data["content_hash"]),
            size_bytes=int(data["size_bytes"]),
            storage_reference=str(data["storage_reference"]),
            review_required=bool(data.get("review_required", True)),
            review_id=None if data.get("review_id") is None else str(data["review_id"]),
            pipeline_artifact_id=None if data.get("pipeline_artifact_id") is None else str(data["pipeline_artifact_id"]),
            max_size_bytes=int(data.get("max_size_bytes", 10 * 1024 * 1024)),
        )
    except (KeyError, TypeError, ValueError, BridgeDomainError) as exc:
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc
