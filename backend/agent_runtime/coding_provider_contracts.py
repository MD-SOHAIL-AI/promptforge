"""Provider-neutral contracts for ForgeX coding providers.

These contracts describe eligibility and generation/review handoff only. They
cannot grant active-workspace, apply, build, flash, or monitor authority.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any


class CodingProviderType(str, Enum):
    VERIFIED_TEMPLATE = "verified_template"
    API_CODING_AGENT = "api_coding_agent"
    CODEX_CLI = "codex_cli"
    AGY_CLI = "agy_cli"
    CLAUDE_CODE_CLI = "claude_code_cli"
    OPENCODE_CLI = "opencode_cli"
    MANUAL_PATCH = "manual_patch"


class CodingProviderCapability(str, Enum):
    GENERATE_FILES = "generate_files"
    MODIFY_FILES = "modify_files"
    SUGGEST_COMMANDS = "suggest_commands"
    READ_SELECTED_CONTEXT = "read_selected_context"
    RUN_IN_CLI_SANDBOX = "run_in_cli_sandbox"
    CREATE_PATCH = "create_patch"
    CREATE_REVIEW = "create_review"


class CodingProviderExecutionMode(str, Enum):
    VERIFIED_TEMPLATE = "verified_template"
    STRUCTURED_PROPOSAL = "structured_proposal"
    MANAGED_CLI_SANDBOX = "managed_cli_sandbox"
    MANUAL_IMPORT = "manual_import"


class CodingProviderReadiness(str, Enum):
    READY = "ready"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"
    BLOCKED = "blocked"


class CodingProviderFailureCode(str, Enum):
    INVALID_REQUEST = "CODING_PROVIDER_INVALID_REQUEST"
    UNIFIED_CODING_WORKFLOW_DISABLED = "UNIFIED_CODING_WORKFLOW_DISABLED"
    WORKFLOW_PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"
    LEGACY_WORKFLOW_ADMISSION_DISABLED = "LEGACY_WORKFLOW_ADMISSION_DISABLED"
    LEGACY_WORKFLOW_READ_ONLY = "LEGACY_WORKFLOW_READ_ONLY"
    PROVIDER_NOT_FOUND = "CODING_PROVIDER_NOT_FOUND"
    PROVIDER_DISABLED = "CODING_PROVIDER_DISABLED"
    PROVIDER_UNREADY = "CODING_PROVIDER_UNREADY"
    DEV_PROVIDER_NOT_ALLOWED = "CODING_PROVIDER_DEV_NOT_ALLOWED"
    CAPABILITY_MISMATCH = "CODING_PROVIDER_CAPABILITY_MISMATCH"
    CONTEXT_MODE_UNSUPPORTED = "CODING_PROVIDER_CONTEXT_UNSUPPORTED"
    NO_COMPATIBLE_PROVIDER = "CODING_PROVIDER_NO_COMPATIBLE_PROVIDER"
    PROVIDER_FAILED = "CODING_PROVIDER_FAILED"
    PROVIDER_UNAVAILABLE = "CODING_PROVIDER_UNAVAILABLE"
    PROVIDER_NOT_IMPLEMENTED = "CODING_PROVIDER_NOT_IMPLEMENTED"
    VALIDATION_FAILED = "CODING_PROVIDER_VALIDATION_FAILED"
    API_CONTRACT_INVALID = "API_CODING_AGENT_CONTRACT_INVALID"
    API_CODING_CONTEXT_INVALID = "API_CODING_CONTEXT_INVALID"
    API_CODING_CONTEXT_EMPTY = "API_CODING_CONTEXT_EMPTY"
    API_CODING_MODEL_CALL_FAILED = "API_CODING_MODEL_CALL_FAILED"
    API_CODING_MODEL_OUTPUT_INVALID = "API_CODING_MODEL_OUTPUT_INVALID"
    UNSAFE_OUTPUT = "CODING_PROVIDER_UNSAFE_OUTPUT"
    OVERSIZED_OUTPUT = "CODING_PROVIDER_OVERSIZED_OUTPUT"
    FAKE_API_CODING_AGENT_DISABLED = "FAKE_API_CODING_AGENT_DISABLED"
    REAL_API_CODING_AGENT_DISABLED = "REAL_API_CODING_AGENT_DISABLED"
    REAL_API_CODING_AGENT_UNAVAILABLE = "REAL_API_CODING_AGENT_UNAVAILABLE"
    CANCELLED = "CODING_PROVIDER_CANCELLED"
    TIMED_OUT = "CODING_PROVIDER_TIMED_OUT"


class CodingRunMode(str, Enum):
    VERIFIED_TEMPLATE = "verified_template"
    STRUCTURED_PROPOSAL = "structured_proposal"
    SANDBOX_REVIEW = "sandbox_review"
    MANUAL_IMPORT = "manual_import"


class CodingRunStage(str, Enum):
    SELECTION = "selection"
    GENERATION = "generation"
    VALIDATION = "validation"
    REVIEW = "review"


class CodingRunStatus(str, Enum):
    QUEUED = "queued"
    VALIDATING = "validating"
    PREPARING_SANDBOX = "preparing_sandbox"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    AWAITING_APPLY = "awaiting_apply"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class CodingContextMode(str, Enum):
    NONE = "none"
    FILE_TREE_ONLY = "file_tree_only"
    SELECTED_FILES = "selected_files"
    BOUNDED_RELEVANT_FILES = "bounded_relevant_files"
    FULL_SMALL_PROJECT = "full_small_project"


FORBIDDEN_PROVIDER_AUTHORITIES = frozenset({
    "apply_patch",
    "run_build",
    "run_flash",
    "open_monitor",
    "active_workspace_mutation",
})

_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SECRET_KEY_MARKERS = ("api_key", "apikey", "credential", "password", "secret", "token")


class CodingProviderContractError(ValueError):
    def __init__(self, code: CodingProviderFailureCode, message: str) -> None:
        self.code = code
        super().__init__(message)


def validate_provider_id(provider_id: object) -> str:
    if not isinstance(provider_id, str) or not _PROVIDER_ID_RE.fullmatch(provider_id):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "provider_id must start with a lowercase letter and contain only lowercase letters, digits, or underscores",
        )
    return provider_id


def ensure_provider_capabilities_safe(
    capabilities: Iterable[object],
) -> frozenset[CodingProviderCapability]:
    if isinstance(capabilities, (str, bytes, bytearray)):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "capabilities must be a collection",
        )
    try:
        values = tuple(capabilities)
    except TypeError as exc:
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "capabilities must be a collection",
        ) from exc
    forbidden = sorted(
        str(item.value if isinstance(item, Enum) else item)
        for item in values
        if str(item.value if isinstance(item, Enum) else item) in FORBIDDEN_PROVIDER_AUTHORITIES
    )
    if forbidden:
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            f"forbidden coding-provider authority: {', '.join(forbidden)}",
        )
    if any(not isinstance(item, CodingProviderCapability) for item in values):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "capabilities must contain CodingProviderCapability values",
        )
    return frozenset(values)


@dataclass(frozen=True, slots=True)
class CodingProviderDescriptor:
    provider_id: str
    provider_type: CodingProviderType
    display_name: str
    execution_mode: CodingProviderExecutionMode
    capabilities: frozenset[CodingProviderCapability]
    context_modes: frozenset[CodingContextMode]
    readiness: CodingProviderReadiness
    enabled: bool
    dev_only: bool = False
    production_eligible: bool = False
    uses_api_keys: bool = False
    uses_local_cli_auth: bool = False
    can_mutate_active_workspace: bool = False
    safe_failure_reason: str | None = None

    def __post_init__(self) -> None:
        validate_provider_descriptor(self)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type.value,
            "display_name": self.display_name,
            "execution_mode": self.execution_mode.value,
            "capabilities": sorted(item.value for item in self.capabilities),
            "context_modes": sorted(item.value for item in self.context_modes),
            "readiness": self.readiness.value,
            "enabled": self.enabled,
            "dev_only": self.dev_only,
            "production_eligible": self.production_eligible,
            "uses_api_keys": self.uses_api_keys,
            "uses_local_cli_auth": self.uses_local_cli_auth,
            "can_mutate_active_workspace": False,
            "safe_failure_reason": self.safe_failure_reason,
        }


def validate_provider_descriptor(descriptor: object) -> CodingProviderDescriptor:
    if not isinstance(descriptor, CodingProviderDescriptor):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "provider must be a CodingProviderDescriptor",
        )
    validate_provider_id(descriptor.provider_id)
    _require_enum(descriptor.provider_type, CodingProviderType, "provider_type")
    _require_enum(descriptor.execution_mode, CodingProviderExecutionMode, "execution_mode")
    _require_enum(descriptor.readiness, CodingProviderReadiness, "readiness")
    _require_text(descriptor.display_name, "display_name", max_chars=200)
    for field_name in (
        "enabled",
        "dev_only",
        "production_eligible",
        "uses_api_keys",
        "uses_local_cli_auth",
        "can_mutate_active_workspace",
    ):
        if not isinstance(getattr(descriptor, field_name), bool):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} must be boolean")
    safe_capabilities = ensure_provider_capabilities_safe(descriptor.capabilities)
    context_modes = _enum_set(descriptor.context_modes, CodingContextMode, "context_modes")
    if not context_modes:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "context_modes cannot be empty")
    if descriptor.can_mutate_active_workspace:
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "coding providers cannot mutate the active workspace",
        )
    if descriptor.dev_only and descriptor.production_eligible:
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "dev-only providers cannot be production eligible",
        )
    if descriptor.production_eligible and (not descriptor.enabled or descriptor.readiness is not CodingProviderReadiness.READY):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "production-eligible providers must be enabled and ready",
        )
    if descriptor.enabled and descriptor.readiness is CodingProviderReadiness.DISABLED:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "enabled provider cannot be disabled")
    if not descriptor.enabled and descriptor.readiness is CodingProviderReadiness.READY:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "disabled provider cannot be ready")
    if (not descriptor.enabled or descriptor.readiness is not CodingProviderReadiness.READY) and not _optional_text(
        descriptor.safe_failure_reason, "safe_failure_reason", max_chars=500
    ):
        raise CodingProviderContractError(
            CodingProviderFailureCode.INVALID_REQUEST,
            "disabled or unready provider requires a safe_failure_reason",
        )
    object.__setattr__(descriptor, "capabilities", safe_capabilities)
    object.__setattr__(descriptor, "context_modes", context_modes)
    return descriptor


@dataclass(frozen=True, slots=True)
class CodingProviderRunRequest:
    run_id: str
    task_id: str
    project_id: str
    provider_id: str
    prompt: str = field(repr=False)
    workspace_root_display: str
    context_mode: CodingContextMode
    requested_capabilities: frozenset[CodingProviderCapability] = frozenset()
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in ("run_id", "task_id", "project_id"):
            _require_text(getattr(self, name), name, max_chars=128)
        validate_provider_id(self.provider_id)
        _require_text(self.prompt, "prompt", max_chars=100_000)
        _require_text(self.workspace_root_display, "workspace_root_display", max_chars=2_000)
        _require_enum(self.context_mode, CodingContextMode, "context_mode")
        object.__setattr__(self, "requested_capabilities", ensure_provider_capabilities_safe(self.requested_capabilities))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_safe_dict(self) -> dict[str, object]:
        encoded = self.prompt.encode("utf-8")
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "prompt_sha256": hashlib.sha256(encoded).hexdigest(),
            "prompt_chars": len(self.prompt),
            "workspace_root_display": self.workspace_root_display,
            "context_mode": self.context_mode.value,
            "requested_capabilities": sorted(item.value for item in self.requested_capabilities),
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CodingProviderEvent:
    event_id: str
    sequence: int
    run_id: str
    event_type: str
    stage: CodingRunStage
    status: CodingRunStatus
    safe_message: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def __post_init__(self) -> None:
        _require_text(self.event_id, "event_id", max_chars=128)
        _require_text(self.run_id, "run_id", max_chars=128)
        _require_text(self.event_type, "event_type", max_chars=128)
        _require_enum(self.stage, CodingRunStage, "stage")
        _require_enum(self.status, CodingRunStatus, "status")
        _require_text(self.safe_message, "safe_message", max_chars=2_000)
        _require_text(self.timestamp, "timestamp", max_chars=64)
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 0:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "sequence must be non-negative")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "stage": self.stage.value,
            "status": self.status.value,
            "safe_message": self.safe_message,
            "metadata": _thaw(self.metadata),
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True, slots=True)
class CodingProviderRunResult:
    status: CodingRunStatus
    summary: str
    review_id: str | None = None
    files_changed: tuple[str, ...] = ()
    events: tuple[CodingProviderEvent, ...] = ()
    failure_code: CodingProviderFailureCode | None = None
    safe_message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_enum(self.status, CodingRunStatus, "status")
        _require_text(self.summary, "summary", max_chars=2_000, allow_empty=True)
        _optional_text(self.review_id, "review_id", max_chars=128)
        _require_text(self.safe_message, "safe_message", max_chars=2_000, allow_empty=True)
        files = tuple(self.files_changed)
        if any(not _safe_diff_path(path) for path in files):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "files_changed contains an invalid path")
        if len(set(path.casefold() for path in files)) != len(files):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "files_changed contains duplicates")
        events = tuple(self.events)
        if any(not isinstance(event, CodingProviderEvent) for event in events):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "events must contain CodingProviderEvent values")
        if any(left.sequence >= right.sequence for left, right in zip(events, events[1:])):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "event sequences must be strictly increasing")
        failed = self.status in {CodingRunStatus.FAILED, CodingRunStatus.CANCELLED, CodingRunStatus.TIMED_OUT}
        if self.failure_code is not None:
            _require_enum(self.failure_code, CodingProviderFailureCode, "failure_code")
        if failed and self.failure_code is None:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "terminal failure requires failure_code")
        if failed and not self.safe_message.strip():
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "terminal failure requires safe_message")
        if not failed and self.failure_code is not None:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "successful state cannot include failure_code")
        object.__setattr__(self, "files_changed", files)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "review_id": self.review_id,
            "files_changed": list(self.files_changed),
            "events": [event.to_safe_dict() for event in self.events],
            "failure_code": self.failure_code.value if self.failure_code else None,
            "safe_message": self.safe_message,
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class CodingProviderSelectionRequest:
    prompt: str = field(repr=False)
    explicit_provider_id: str | None = None
    required_capabilities: frozenset[CodingProviderCapability] = frozenset()
    context_mode: CodingContextMode = CodingContextMode.SELECTED_FILES
    allow_dev_providers: bool = False

    def __post_init__(self) -> None:
        _require_text(self.prompt, "prompt", max_chars=100_000)
        if self.explicit_provider_id is not None:
            validate_provider_id(self.explicit_provider_id)
        object.__setattr__(self, "required_capabilities", ensure_provider_capabilities_safe(self.required_capabilities))
        _require_enum(self.context_mode, CodingContextMode, "context_mode")
        if not isinstance(self.allow_dev_providers, bool):
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "allow_dev_providers must be boolean")


@dataclass(frozen=True, slots=True)
class CodingProviderSelectionResult:
    provider: CodingProviderDescriptor | None
    failure_code: CodingProviderFailureCode | None = None
    safe_message: str = ""
    considered_provider_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.provider is not None:
            validate_provider_descriptor(self.provider)
        if self.failure_code is not None:
            _require_enum(self.failure_code, CodingProviderFailureCode, "failure_code")
        if self.provider is None and self.failure_code is None:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "failed selection requires failure_code")
        if self.provider is not None and self.failure_code is not None:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "successful selection cannot include failure_code")
        _require_text(self.safe_message, "safe_message", max_chars=2_000, allow_empty=self.provider is not None)
        ids = tuple(validate_provider_id(item) for item in self.considered_provider_ids)
        object.__setattr__(self, "considered_provider_ids", ids)

    @property
    def selected(self) -> bool:
        return self.provider is not None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "selected": self.selected,
            "provider": self.provider.to_safe_dict() if self.provider else None,
            "failure_code": self.failure_code.value if self.failure_code else None,
            "safe_message": self.safe_message,
            "considered_provider_ids": list(self.considered_provider_ids),
        }


def _require_enum(value: object, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} is invalid")


def _safe_diff_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    windows = PureWindowsPath(value)
    if value.startswith("/") or PurePosixPath(value).is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def _enum_set(value: object, enum_type: type[Enum], field_name: str) -> frozenset[Any]:
    if isinstance(value, (str, bytes, bytearray)):
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} must be a collection")
    try:
        items = frozenset(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} must be a collection") from exc
    if any(not isinstance(item, enum_type) for item in items):
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} contains invalid values")
    return items


def _require_text(value: object, field_name: str, *, max_chars: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > max_chars or (not allow_empty and not value.strip()):
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{field_name} must be UTF-8 text") from exc
    return value


def _optional_text(value: object, field_name: str, *, max_chars: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name, max_chars=max_chars)


def _freeze_metadata(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, "metadata must be a mapping")
    return _freeze_mapping(value, "metadata", set())


def _freeze_mapping(value: Mapping[object, Any], path: str, active: set[int]) -> Mapping[str, Any]:
    identity = id(value)
    if identity in active:
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{path} cannot be cyclic")
    active.add(identity)
    try:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or any(marker in key.casefold() for marker in _SECRET_KEY_MARKERS):
                raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{path} contains an unsafe key")
            result[key] = _freeze_value(item, f"{path}.{key}", active)
        return MappingProxyType(result)
    finally:
        active.remove(identity)


def _freeze_value(value: Any, path: str, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{path} must be finite")
    if isinstance(value, Mapping):
        return _freeze_mapping(value, path, active)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in active:
            raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{path} cannot be cyclic")
        active.add(identity)
        try:
            return tuple(_freeze_value(item, f"{path}[{index}]", active) for index, item in enumerate(value))
        finally:
            active.remove(identity)
    raise CodingProviderContractError(CodingProviderFailureCode.INVALID_REQUEST, f"{path} is not JSON-compatible")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value
