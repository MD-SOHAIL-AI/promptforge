"""Stable public contracts shared by API, CLI-agent and template providers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ProviderType(str, Enum):
    API = "api_provider"
    AGENT = "agent_provider"
    TEMPLATE = "template_provider"


class AuthType(str, Enum):
    API_KEY = "api_key"
    CLI_SESSION = "cli_session"
    NONE = "none"


class ProviderState(str, Enum):
    READY = "ready"
    MISSING_API_KEY = "missing_api_key"
    CLI_NOT_FOUND = "cli_not_found"
    NOT_LOGGED_IN = "not_logged_in"
    RATE_LIMITED = "rate_limited"
    USAGE_LIMIT_REACHED = "usage_limit_reached"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    DISABLED = "disabled"
    UNSUPPORTED_PROJECT = "unsupported_project"
    NOT_CONFIGURED = "not_configured"


class GenerationStatus(str, Enum):
    STARTED = "generation_started"
    COMPLETED = "generation_completed"
    CONTENT_VERIFIED = "content_verified"
    NO_OP_SUCCESS = "no_op_success"
    FAILED = "generation_failed"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMITED = "rate_limited"
    USAGE_LIMIT_REACHED = "usage_limit_reached"
    INVALID_OUTPUT = "invalid_output"


class WorkspaceMode(str, Enum):
    MODIFY_EXISTING = "modify_existing_project"
    GENERATE_INTO_FOLDER = "generate_into_open_folder"


class ProviderErrorCode(str, Enum):
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_API_KEY = "INVALID_API_KEY"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    CLI_NOT_FOUND = "CLI_NOT_FOUND"
    CLI_NOT_LOGGED_IN = "CLI_NOT_LOGGED_IN"
    CLI_USAGE_LIMIT_REACHED = "CLI_USAGE_LIMIT_REACHED"
    CLI_PERMISSION_BLOCKED = "CLI_PERMISSION_BLOCKED"
    WORKSPACE_NOT_TRUSTED = "WORKSPACE_NOT_TRUSTED"
    INVALID_GENERATED_OUTPUT = "INVALID_GENERATED_OUTPUT"
    CONTENT_VERIFICATION_FAILED = "CONTENT_VERIFICATION_FAILED"
    PATCH_APPLY_FAILED = "PATCH_APPLY_FAILED"
    BUILD_FAILED = "BUILD_FAILED"
    FLASH_FAILED = "FLASH_FAILED"
    MONITOR_FAILED = "MONITOR_FAILED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class StructuredRunEvent:
    run_id: str
    stage: str
    status: str
    provider_id: str
    provider_type: ProviderType
    generation_source: str
    message: str
    safe_summary: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)

    def to_safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["provider_type"] = self.provider_type.value
        return value


@dataclass(slots=True)
class ForgeXRunSummary:
    run_id: str
    requested_provider: str
    actual_provider: str | None = None
    provider_type: ProviderType | None = None
    selected_model: str | None = None
    auth_status: str = "unknown"
    generation_source: str | None = None
    workspace_mode: WorkspaceMode = WorkspaceMode.GENERATE_INTO_FOLDER
    project_type: str = "generic"
    changed_files: list[str] = field(default_factory=list)
    unchanged_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    no_op_status: bool = False
    content_verification_status: str = "pending"
    generation_status: str = GenerationStatus.STARTED.value
    build_status: str = "not_started"
    flash_status: str = "not_started"
    monitor_status: str = "not_started"
    fallback_used: bool = False
    fallback_reason: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_suggested_action: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["provider_type"] = self.provider_type.value if self.provider_type else None
        value["workspace_mode"] = self.workspace_mode.value
        return value
