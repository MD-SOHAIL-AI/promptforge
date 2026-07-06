"""Provider-neutral bridge data contracts with safe public serialization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import BridgeDomainError, BridgeErrorCode
from .validation import (
    MAX_TIMEOUT_SECONDS,
    MIN_TIMEOUT_SECONDS,
    IMPLEMENTED_GENERIC_PROVIDER_IDS,
    ensure_utc,
    format_datetime,
    sanitize_message,
    utc_now,
    validate_identifier,
    validate_provider_id,
    validate_safe_code,
)


class BridgeArtifactType(str, Enum):
    DIFF = "diff"
    PATCH = "patch"
    MANIFEST = "manifest"
    REVIEW = "review"
    LOG_SUMMARY = "log_summary"
    DIAGNOSTIC = "diagnostic"


class BridgeEventType(str, Enum):
    STATE_CHANGED = "state_changed"
    PROGRESS = "progress"
    ARTIFACT_CREATED = "artifact_created"
    WARNING = "warning"
    FAILURE = "failure"
    CANCELLATION_REQUESTED = "cancellation_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    RESYNC_REQUIRED = "resync_required"


class BridgeCancellationDisposition(str, Enum):
    ACCEPTED = "accepted"
    ALREADY_REQUESTED = "already_requested"
    ALREADY_TERMINAL = "already_terminal"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"


class BridgeCancellationEscalation(str, Enum):
    NONE = "none"
    COOPERATIVE = "cooperative"
    PROCESS_TERMINATION = "process_termination"


@dataclass(frozen=True, slots=True)
class BridgeCapabilities:
    provider_id: str
    display_name: str
    provider_version: str | None = None
    capabilities_version: int = 1
    available: bool = False
    non_interactive: bool = False
    sandbox_required: bool = True
    supports_streaming: bool = False
    supports_cancellation: bool = False
    supports_timeout: bool = False
    supports_artifacts: bool = False
    supports_structured_output: bool = False
    supports_subscription_auth: bool = False
    supports_api_key_auth: bool = False
    supports_resume: bool = False

    def __post_init__(self) -> None:
        validate_provider_id(self.provider_id)
        if not self.display_name.strip() or len(self.display_name) > 100:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Provider display name is invalid.")
        if self.capabilities_version != 1:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Capability schema version is unsupported.")
        if not self.sandbox_required:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_REQUIRED)
        if self.available and self.provider_id not in IMPLEMENTED_GENERIC_PROVIDER_IDS:
            raise BridgeDomainError(BridgeErrorCode.PROVIDER_DISABLED)
        object.__setattr__(self, "provider_version", sanitize_message(self.provider_version))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "provider_version": self.provider_version,
            "capabilities_version": self.capabilities_version,
            "available": self.available,
            "non_interactive": self.non_interactive,
            "sandbox_required": True,
            "supports_streaming": self.supports_streaming,
            "supports_cancellation": self.supports_cancellation,
            "supports_timeout": self.supports_timeout,
            "supports_artifacts": self.supports_artifacts,
            "supports_structured_output": self.supports_structured_output,
            "supports_subscription_auth": self.supports_subscription_auth,
            "supports_api_key_auth": self.supports_api_key_auth,
            "supports_resume": self.supports_resume,
        }


@dataclass(frozen=True, slots=True)
class BridgeDetectionResult:
    provider_id: str
    installed: bool = False
    available: bool = False
    safe_message: str = "Provider detection is not connected."
    provider_version: str | None = None
    authentication_status: str = "unknown"
    unavailability_code: str | None = None
    capabilities: BridgeCapabilities | None = None

    def __post_init__(self) -> None:
        validate_provider_id(self.provider_id)
        if self.available and self.provider_id not in IMPLEMENTED_GENERIC_PROVIDER_IDS:
            raise BridgeDomainError(BridgeErrorCode.PROVIDER_DISABLED)
        if self.authentication_status not in {
            "authenticated",
            "unauthenticated",
            "unknown",
            "not_installed",
            "error",
        }:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Authentication status is invalid.")
        if self.unavailability_code is not None:
            validate_safe_code(self.unavailability_code, "unavailability_code")
        if self.capabilities is not None and self.capabilities.provider_id != self.provider_id:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Detection capabilities do not match provider.")
        object.__setattr__(self, "safe_message", sanitize_message(self.safe_message) or "")
        object.__setattr__(self, "provider_version", sanitize_message(self.provider_version))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "installed": self.installed,
            "available": self.available,
            "safe_message": self.safe_message,
            "provider_version": self.provider_version,
            "authentication_status": self.authentication_status,
            "unavailability_code": self.unavailability_code,
            "capabilities": self.capabilities.to_public_dict() if self.capabilities else None,
        }


@dataclass(frozen=True, slots=True)
class BridgeValidationResult:
    valid: bool
    failure_code: str | None = None
    safe_message: str | None = None

    def __post_init__(self) -> None:
        if self.failure_code is not None:
            validate_safe_code(self.failure_code, "failure_code")
        object.__setattr__(self, "safe_message", sanitize_message(self.safe_message))


@dataclass(frozen=True, slots=True)
class BridgeRunResult:
    succeeded: bool
    artifact_ids: tuple[str, ...] = ()
    artifacts: tuple["BridgeArtifactReference", ...] = ()
    failure_code: str | None = None
    safe_message: str | None = None
    final_status: str | None = None
    review_id: str | None = None
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        for artifact_id in self.artifact_ids:
            validate_identifier(artifact_id, "artifact_id")
        if tuple(item.artifact_id for item in self.artifacts) != self.artifact_ids:
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID, "Artifact identifiers do not match references.")
        if self.failure_code is not None:
            validate_safe_code(self.failure_code, "failure_code")
        if self.final_status is not None:
            validate_safe_code(self.final_status, "final_status")
        if self.review_id is not None:
            validate_identifier(self.review_id, "review_id")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Run duration is invalid.")
        object.__setattr__(self, "safe_message", sanitize_message(self.safe_message))

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "succeeded": self.succeeded,
            "artifact_ids": list(self.artifact_ids),
            "artifacts": [item.to_public_dict() for item in self.artifacts],
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
            "final_status": self.final_status,
            "review_id": self.review_id,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class BridgeCancellationResult:
    disposition: BridgeCancellationDisposition
    run_id: str
    requested_at: datetime | None = None
    reason_code: str | None = None
    escalation: BridgeCancellationEscalation = BridgeCancellationEscalation.COOPERATIVE

    def __post_init__(self) -> None:
        validate_identifier(self.run_id, "run_id")
        if self.requested_at is not None:
            object.__setattr__(self, "requested_at", ensure_utc(self.requested_at, "requested_at"))
        if self.reason_code is not None:
            validate_safe_code(self.reason_code, "reason_code")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition.value,
            "run_id": self.run_id,
            "requested_at": format_datetime(self.requested_at),
            "reason_code": self.reason_code,
            "escalation": self.escalation.value,
        }


@dataclass(frozen=True, slots=True)
class BridgeRunRequest:
    """Orchestration input. ``instruction`` is runtime-only and never serialized."""

    run_id: str
    provider_id: str
    project_id: str
    sandbox_id: str
    instruction: str = field(repr=False)
    requested_capability: str = "edit_files"
    timeout_seconds: int = 300
    created_at: datetime = field(default_factory=utc_now)
    correlation_id: str = "bridge-correlation"
    execution_mode: str = "generic"

    def __post_init__(self) -> None:
        validate_identifier(self.run_id, "run_id")
        validate_provider_id(self.provider_id)
        validate_identifier(self.project_id, "project_id")
        validate_identifier(self.sandbox_id, "sandbox_id")
        validate_identifier(self.correlation_id, "correlation_id")
        if self.execution_mode not in {"generic", "legacy"}:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Execution mode is invalid.")
        validate_safe_code(self.requested_capability, "requested_capability")
        if not isinstance(self.instruction, str) or not self.instruction.strip():
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Instruction is required.")
        if not MIN_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Bridge timeout is outside safe bounds.")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, "created_at"))

    @property
    def instruction_hash(self) -> str:
        return hashlib.sha256(self.instruction.encode("utf-8")).hexdigest()

    @property
    def instruction_length(self) -> int:
        return len(self.instruction)

    def to_safe_metadata(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "project_id": self.project_id,
            "sandbox_id": self.sandbox_id,
            "requested_capability": self.requested_capability,
            "timeout_seconds": self.timeout_seconds,
            "created_at": format_datetime(self.created_at),
            "correlation_id": self.correlation_id,
            "execution_mode": self.execution_mode,
            "instruction_hash": self.instruction_hash,
            "instruction_length": self.instruction_length,
        }


@dataclass(frozen=True, slots=True)
class BridgeSandboxContext:
    sandbox_id: str
    run_id: str
    project_id: str
    creation_status: str
    cleanup_policy: str
    containment_verified: bool
    internal_sandbox_root: Path = field(repr=False)
    managed_sandbox_root: Path = field(repr=False)
    active_workspace_root: Path = field(repr=False)

    def __post_init__(self) -> None:
        validate_identifier(self.sandbox_id, "sandbox_id")
        validate_identifier(self.run_id, "run_id")
        validate_identifier(self.project_id, "project_id")
        if self.creation_status not in {"created", "ready"}:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_INVALID)
        if self.cleanup_policy not in {"retain_for_review", "cleanup_after_terminal"}:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_INVALID)
        if not self.containment_verified:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED)
        sandbox = self.internal_sandbox_root.expanduser().resolve()
        managed = self.managed_sandbox_root.expanduser().resolve()
        active = self.active_workspace_root.expanduser().resolve()
        try:
            sandbox.relative_to(managed)
        except ValueError as exc:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED) from exc
        if sandbox == managed or sandbox == active or _paths_overlap(sandbox, active) or _paths_overlap(managed, active):
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED)
        object.__setattr__(self, "internal_sandbox_root", sandbox)
        object.__setattr__(self, "managed_sandbox_root", managed)
        object.__setattr__(self, "active_workspace_root", active)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "creation_status": self.creation_status,
            "cleanup_policy": self.cleanup_policy,
            "containment_verified": self.containment_verified,
        }


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class BridgeRunContext:
    request: BridgeRunRequest
    sandbox: BridgeSandboxContext

    def __post_init__(self) -> None:
        if self.request.run_id != self.sandbox.run_id:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Run and sandbox identifiers do not match.")
        if self.request.project_id != self.sandbox.project_id:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Project and sandbox identifiers do not match.")
        if self.request.sandbox_id != self.sandbox.sandbox_id:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Sandbox identifiers do not match.")


@dataclass(frozen=True, slots=True)
class BridgeRunEvent:
    event_id: str
    run_id: str
    sequence: int
    timestamp: datetime
    event_type: BridgeEventType
    status: str | None = None
    safe_message: str | None = None
    progress: int | None = None
    failure_code: str | None = None
    artifact_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.event_id, "event_id")
        validate_identifier(self.run_id, "run_id")
        if self.sequence < 1:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Event sequence must start at one.")
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp, "timestamp"))
        object.__setattr__(self, "safe_message", sanitize_message(self.safe_message))
        if self.progress is not None and not 0 <= self.progress <= 100:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Event progress must be between 0 and 100.")
        if self.failure_code is not None:
            validate_safe_code(self.failure_code, "failure_code")
        if self.artifact_id is not None:
            validate_identifier(self.artifact_id, "artifact_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": format_datetime(self.timestamp),
            "event_type": self.event_type.value,
            "status": self.status,
            "safe_message": self.safe_message,
            "progress": self.progress,
            "failure_code": self.failure_code,
            "artifact_id": self.artifact_id,
        }


@dataclass(frozen=True, slots=True)
class BridgeArtifactReference:
    artifact_id: str
    run_id: str
    artifact_type: BridgeArtifactType
    created_at: datetime
    content_hash: str
    size_bytes: int
    storage_reference: str = field(repr=False)
    review_required: bool = True
    review_id: str | None = None
    pipeline_artifact_id: str | None = None
    max_size_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        validate_identifier(self.artifact_id, "artifact_id")
        validate_identifier(self.run_id, "run_id")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, "created_at"))
        if len(self.content_hash) != 64 or any(char not in "0123456789abcdef" for char in self.content_hash):
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
        if self.size_bytes < 0 or self.size_bytes > self.max_size_bytes:
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
        _validate_storage_reference(self.storage_reference)
        if self.artifact_type in {BridgeArtifactType.PATCH, BridgeArtifactType.DIFF}:
            if not self.review_required or not self.review_id or not self.pipeline_artifact_id:
                raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
            validate_identifier(self.review_id, "review_id")
            validate_identifier(self.pipeline_artifact_id, "pipeline_artifact_id")

    def resolve_internal(self, managed_root: Path) -> Path:
        root = managed_root.expanduser().resolve()
        target = (root / PurePosixPath(self.storage_reference)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID) from exc
        return target

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "artifact_type": self.artifact_type.value,
            "created_at": format_datetime(self.created_at),
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "review_required": self.review_required,
            "review_id": self.review_id,
            "pipeline_artifact_id": self.pipeline_artifact_id,
            "max_size_bytes": self.max_size_bytes,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        return {**self.to_public_dict(), "storage_reference": self.storage_reference}


def _validate_storage_reference(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
    path = PurePosixPath(value)
    if path.is_absolute() or ":" in path.parts[0] or any(part in {"", ".", ".."} for part in path.parts):
        raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
