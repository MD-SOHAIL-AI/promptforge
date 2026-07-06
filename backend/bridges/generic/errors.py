"""Stable provider-neutral bridge errors and public normalization."""

from __future__ import annotations

from enum import Enum
from typing import Any


class BridgeErrorCode(str, Enum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_DISABLED = "provider_disabled"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    PROVIDER_NOT_REVIEW_ELIGIBLE = "provider_not_review_eligible"
    PROVIDER_PAUSED = "provider_paused"
    PROVIDER_NATIVE_WRITE_UNPROVEN = "provider_native_write_unproven"
    PROVIDER_PERMISSION_BLOCKED = "provider_permission_blocked"
    PROVIDER_ARTIFACT_UNRELIABLE = "provider_artifact_unreliable"
    PROVIDER_HEADLESS_UNSUPPORTED = "provider_headless_unsupported"
    PROVIDER_STRATEGY_DECISION_REQUIRED = "provider_strategy_decision_required"
    LOCAL_CLI_PROVIDERS_PAUSED = "local_cli_providers_paused"
    PROVIDER_RUNTIME_NOT_READY = "provider_runtime_not_ready"
    PROVIDER_REQUIRES_FORGEX_TOOL_RUNTIME = "provider_requires_forgex_tool_runtime"
    PROVIDER_API_DESIGN_ONLY = "provider_api_design_only"
    PROVIDER_NOT_ROUTEABLE = "provider_not_routeable"
    INVALID_REQUEST = "invalid_request"
    INVALID_TRANSITION = "invalid_transition"
    SANDBOX_REQUIRED = "sandbox_required"
    SANDBOX_INVALID = "sandbox_invalid"
    SANDBOX_ESCAPE_BLOCKED = "sandbox_escape_blocked"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PROCESS_START_FAILED = "process_start_failed"
    PROCESS_FAILED = "process_failed"
    NO_CHANGES_PRODUCED = "no_changes_produced"
    ARTIFACT_INVALID = "artifact_invalid"
    PERSISTENCE_FAILED = "persistence_failed"
    RECORD_CORRUPT = "record_corrupt"
    INTERNAL_ERROR = "internal_error"


DEFAULT_SAFE_MESSAGES: dict[BridgeErrorCode, str] = {
    BridgeErrorCode.PROVIDER_UNAVAILABLE: "The requested bridge provider is unavailable.",
    BridgeErrorCode.PROVIDER_DISABLED: "The requested bridge provider is disabled.",
    BridgeErrorCode.PROVIDER_UNSUPPORTED: "The requested bridge provider is not supported.",
    BridgeErrorCode.PROVIDER_NOT_REVIEW_ELIGIBLE: "The provider has not earned review eligibility.",
    BridgeErrorCode.PROVIDER_PAUSED: "The requested bridge provider is paused.",
    BridgeErrorCode.PROVIDER_NATIVE_WRITE_UNPROVEN: "The provider has not passed native write validation.",
    BridgeErrorCode.PROVIDER_PERMISSION_BLOCKED: "The provider is blocked by the ForgeX permission boundary.",
    BridgeErrorCode.PROVIDER_ARTIFACT_UNRELIABLE: "The provider has not passed safe artifact validation.",
    BridgeErrorCode.PROVIDER_HEADLESS_UNSUPPORTED: "The provider has no supported headless mode.",
    BridgeErrorCode.PROVIDER_STRATEGY_DECISION_REQUIRED: "The provider has no approved strategy evidence.",
    BridgeErrorCode.LOCAL_CLI_PROVIDERS_PAUSED: "Local CLI providers are paused.",
    BridgeErrorCode.PROVIDER_RUNTIME_NOT_READY: "The provider runtime is not ready.",
    BridgeErrorCode.PROVIDER_REQUIRES_FORGEX_TOOL_RUNTIME: "The provider requires the ForgeX-owned tool runtime.",
    BridgeErrorCode.PROVIDER_API_DESIGN_ONLY: "The API provider is design-only and disabled.",
    BridgeErrorCode.PROVIDER_NOT_ROUTEABLE: "The provider is not routeable.",
    BridgeErrorCode.INVALID_REQUEST: "The bridge request is invalid.",
    BridgeErrorCode.INVALID_TRANSITION: "The requested run state transition is not allowed.",
    BridgeErrorCode.SANDBOX_REQUIRED: "A verified sandbox is required.",
    BridgeErrorCode.SANDBOX_INVALID: "The bridge sandbox is invalid.",
    BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED: "Sandbox containment validation failed.",
    BridgeErrorCode.TIMEOUT: "The bridge run timed out.",
    BridgeErrorCode.CANCELLED: "The bridge run was cancelled.",
    BridgeErrorCode.PROCESS_START_FAILED: "The bridge provider could not start.",
    BridgeErrorCode.PROCESS_FAILED: "The bridge provider failed.",
    BridgeErrorCode.NO_CHANGES_PRODUCED: "AGY completed but produced no sandbox changes.",
    BridgeErrorCode.ARTIFACT_INVALID: "The bridge artifact reference is invalid.",
    BridgeErrorCode.PERSISTENCE_FAILED: "Bridge run state could not be persisted.",
    BridgeErrorCode.RECORD_CORRUPT: "Persisted bridge run state is invalid.",
    BridgeErrorCode.INTERNAL_ERROR: "An internal bridge error occurred.",
}


class BridgeDomainError(ValueError):
    """Internal typed error with an intentionally small public representation."""

    def __init__(
        self,
        code: BridgeErrorCode,
        safe_message: str | None = None,
        *,
        internal_details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.safe_message = safe_message or DEFAULT_SAFE_MESSAGES[code]
        self.internal_details = internal_details or {}
        super().__init__(self.safe_message)

    def to_public_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.safe_message}


def normalize_bridge_error(error: BaseException) -> BridgeDomainError:
    """Map arbitrary exceptions without exposing exception text or stack data."""

    if isinstance(error, BridgeDomainError):
        return error
    if isinstance(error, TimeoutError):
        return BridgeDomainError(BridgeErrorCode.TIMEOUT)
    return BridgeDomainError(
        BridgeErrorCode.INTERNAL_ERROR,
        internal_details={"exception_type": type(error).__name__},
    )
