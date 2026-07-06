"""Canonical generic bridge run states and orchestration-owned transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .errors import BridgeDomainError, BridgeErrorCode
from .models import BridgeCancellationDisposition, BridgeCancellationResult
from .validation import (
    ensure_utc,
    format_datetime,
    sanitize_message,
    utc_now,
    validate_identifier,
    validate_provider_id,
    validate_safe_code,
)


BRIDGE_RUN_SCHEMA_VERSION = 1


class BridgeRunStatus(str, Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PREPARING_SANDBOX = "preparing_sandbox"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COLLECTING_ARTIFACTS = "collecting_artifacts"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


TERMINAL_STATES = frozenset(
    {
        BridgeRunStatus.COMPLETED,
        BridgeRunStatus.FAILED,
        BridgeRunStatus.CANCELLED,
        BridgeRunStatus.TIMED_OUT,
        BridgeRunStatus.INTERRUPTED,
        BridgeRunStatus.BLOCKED,
    }
)


_TRANSITIONS: dict[BridgeRunStatus, frozenset[BridgeRunStatus]] = {
    BridgeRunStatus.QUEUED: frozenset(
        {BridgeRunStatus.VALIDATING, BridgeRunStatus.CANCELLING, BridgeRunStatus.CANCELLED, BridgeRunStatus.FAILED, BridgeRunStatus.INTERRUPTED}
    ),
    BridgeRunStatus.VALIDATING: frozenset(
        {BridgeRunStatus.PREPARING_SANDBOX, BridgeRunStatus.BLOCKED, BridgeRunStatus.CANCELLING, BridgeRunStatus.FAILED, BridgeRunStatus.INTERRUPTED}
    ),
    BridgeRunStatus.PREPARING_SANDBOX: frozenset(
        {BridgeRunStatus.RUNNING, BridgeRunStatus.CANCELLING, BridgeRunStatus.FAILED, BridgeRunStatus.INTERRUPTED}
    ),
    BridgeRunStatus.RUNNING: frozenset(
        {BridgeRunStatus.COLLECTING_ARTIFACTS, BridgeRunStatus.CANCELLING, BridgeRunStatus.FAILED, BridgeRunStatus.TIMED_OUT, BridgeRunStatus.INTERRUPTED}
    ),
    BridgeRunStatus.CANCELLING: frozenset(
        {BridgeRunStatus.CANCELLED, BridgeRunStatus.FAILED, BridgeRunStatus.INTERRUPTED}
    ),
    BridgeRunStatus.COLLECTING_ARTIFACTS: frozenset(
        {BridgeRunStatus.COMPLETED, BridgeRunStatus.FAILED, BridgeRunStatus.CANCELLING, BridgeRunStatus.INTERRUPTED}
    ),
    **{status: frozenset() for status in TERMINAL_STATES},
}
ALLOWED_TRANSITIONS: Mapping[BridgeRunStatus, frozenset[BridgeRunStatus]] = MappingProxyType(_TRANSITIONS)


@dataclass(frozen=True, slots=True)
class BridgeRunRecord:
    run_id: str
    provider_id: str
    status: BridgeRunStatus
    created_at: datetime
    updated_at: datetime
    project_id: str
    sandbox_id: str
    correlation_id: str
    instruction_hash: str
    instruction_length: int
    execution_mode: str = "generic"
    revision: int = 0
    schema_version: int = BRIDGE_RUN_SCHEMA_VERSION
    started_at: datetime | None = None
    finished_at: datetime | None = None
    event_count: int = 0
    artifact_count: int = 0
    failure_code: str | None = None
    safe_failure_message: str | None = None
    cancellation_requested: bool = False
    cancellation_requested_at: datetime | None = None
    cancellation_reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != BRIDGE_RUN_SCHEMA_VERSION:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        validate_identifier(self.run_id, "run_id")
        validate_provider_id(self.provider_id)
        validate_identifier(self.project_id, "project_id")
        validate_identifier(self.sandbox_id, "sandbox_id")
        validate_identifier(self.correlation_id, "correlation_id")
        if self.execution_mode not in {"generic", "legacy"}:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, ensure_utc(getattr(self, name), name))
        for name in ("started_at", "finished_at", "cancellation_requested_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, name))
        if self.updated_at < self.created_at:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        if self.started_at is not None and self.started_at < self.created_at:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        if self.finished_at is not None and self.finished_at < self.updated_at:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        if len(self.instruction_hash) != 64 or any(char not in "0123456789abcdef" for char in self.instruction_hash):
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        if self.instruction_length < 1 or self.event_count < 0 or self.artifact_count < 0 or self.revision < 0:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
        if self.failure_code is not None:
            validate_safe_code(self.failure_code, "failure_code")
        if self.cancellation_reason_code is not None:
            validate_safe_code(self.cancellation_reason_code, "cancellation_reason_code")
        object.__setattr__(self, "safe_failure_message", sanitize_message(self.safe_failure_message))
        if self.status in TERMINAL_STATES and self.finished_at is None:
            raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "status": self.status.value,
            "created_at": format_datetime(self.created_at),
            "started_at": format_datetime(self.started_at),
            "finished_at": format_datetime(self.finished_at),
            "updated_at": format_datetime(self.updated_at),
            "project_id": self.project_id,
            "sandbox_id": self.sandbox_id,
            "correlation_id": self.correlation_id,
            "instruction_hash": self.instruction_hash,
            "instruction_length": self.instruction_length,
            "execution_mode": self.execution_mode,
            "revision": self.revision,
            "event_count": self.event_count,
            "artifact_count": self.artifact_count,
            "failure_code": self.failure_code,
            "safe_failure_message": self.safe_failure_message,
            "cancellation_requested": self.cancellation_requested,
            "cancellation_requested_at": format_datetime(self.cancellation_requested_at),
            "cancellation_reason_code": self.cancellation_reason_code,
        }


class BridgeRunStateMachine:
    """The only generic component authorized to mutate canonical run history."""

    def transition(
        self,
        record: BridgeRunRecord,
        next_status: BridgeRunStatus,
        *,
        at: datetime | None = None,
        failure_code: str | None = None,
        safe_failure_message: str | None = None,
    ) -> BridgeRunRecord:
        if next_status not in ALLOWED_TRANSITIONS[record.status]:
            raise BridgeDomainError(
                BridgeErrorCode.INVALID_TRANSITION,
                internal_details={"current": record.status.value, "next": next_status.value},
            )
        timestamp = ensure_utc(at or utc_now(), "transition_at")
        if timestamp < record.updated_at:
            raise BridgeDomainError(BridgeErrorCode.INVALID_TRANSITION, "Run timestamps must be monotonic.")
        if failure_code is not None:
            validate_safe_code(failure_code, "failure_code")
        started_at = record.started_at
        if next_status is BridgeRunStatus.RUNNING and started_at is None:
            started_at = timestamp
        finished_at = timestamp if next_status in TERMINAL_STATES else None
        return replace(
            record,
            status=next_status,
            updated_at=timestamp,
            started_at=started_at,
            finished_at=finished_at,
            failure_code=failure_code,
            safe_failure_message=sanitize_message(safe_failure_message),
        )

    def request_cancellation(
        self,
        record: BridgeRunRecord,
        *,
        reason_code: str = "user_requested",
        at: datetime | None = None,
    ) -> tuple[BridgeRunRecord, BridgeCancellationResult]:
        if record.status in TERMINAL_STATES:
            return record, BridgeCancellationResult(
                BridgeCancellationDisposition.ALREADY_TERMINAL,
                record.run_id,
                requested_at=record.cancellation_requested_at,
                reason_code=record.cancellation_reason_code,
            )
        if record.cancellation_requested:
            return record, BridgeCancellationResult(
                BridgeCancellationDisposition.ALREADY_REQUESTED,
                record.run_id,
                requested_at=record.cancellation_requested_at,
                reason_code=record.cancellation_reason_code,
            )
        validate_safe_code(reason_code, "reason_code")
        timestamp = ensure_utc(at or utc_now(), "cancellation_requested_at")
        transitioned = self.transition(record, BridgeRunStatus.CANCELLING, at=timestamp)
        updated = replace(
            transitioned,
            cancellation_requested=True,
            cancellation_requested_at=timestamp,
            cancellation_reason_code=reason_code,
        )
        return updated, BridgeCancellationResult(
            BridgeCancellationDisposition.ACCEPTED,
            record.run_id,
            requested_at=timestamp,
            reason_code=reason_code,
        )

    def reconcile_after_restart(self, record: BridgeRunRecord, *, at: datetime | None = None) -> BridgeRunRecord:
        if record.status in TERMINAL_STATES:
            return record
        return self.transition(
            record,
            BridgeRunStatus.INTERRUPTED,
            at=at,
            failure_code="unexpected_restart",
            safe_failure_message="The bridge run was interrupted by an unexpected restart.",
        )
