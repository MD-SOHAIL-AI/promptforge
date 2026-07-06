"""Bridge detection data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


BridgeProviderType = Literal["tool_bridge"]
BridgeAuthStatus = Literal[
    "authenticated",
    "unauthenticated",
    "unknown",
    "not_installed",
    "error",
]
BridgeStatusConfidence = Literal["high", "medium", "low"]
BridgeSetupAction = Literal["open_docs", "run_official_login_manually", "none"]


@dataclass(frozen=True, slots=True)
class BridgeCapabilities:
    detect: bool = True
    run_prompt: bool = False
    stream_prompt: bool = False
    edit_files: bool = False
    diff_review: bool = False

    def to_dict(self) -> dict[str, bool]:
        return {
            "detect": self.detect,
            "run_prompt": self.run_prompt,
            "stream_prompt": self.stream_prompt,
            "edit_files": self.edit_files,
            "diff_review": self.diff_review,
        }


@dataclass(frozen=True, slots=True)
class BridgeDetectionResult:
    provider_id: str
    display_name: str
    installed: bool
    version: str | None
    executable_path: str | None
    auth_status: BridgeAuthStatus
    auth_message: str
    status_confidence: BridgeStatusConfidence = "low"
    setup_hint: str | None = None
    setup_action: BridgeSetupAction = "run_official_login_manually"
    safe_status_checked: bool = False
    checked_commands: tuple[str, ...] = ()
    capabilities: BridgeCapabilities = field(default_factory=BridgeCapabilities)
    warnings: tuple[str, ...] = ()
    provider_type: BridgeProviderType = "tool_bridge"
    provider_kind: str = "local_cli"
    auth_mode: str = "official_cli_managed"
    execution_mode: str = "detection_only"
    workspace_mode: str = "none"
    production_eligible: bool = False
    qa_only: bool = True
    detection_classification: str = "CLI_DETECTION_UNKNOWN"
    auth_classification: str = "CLI_AUTH_UNKNOWN"
    executable_found: bool = False
    version_detected: bool = False
    login_command: str | None = None
    smoke_ready: bool = False
    smoke_classification: str = "STANDALONE_SMOKE_NOT_READY"
    can_run: bool = False
    reason: str = "Prompt execution is not enabled in this phase"
    last_checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "type": self.provider_type,
            "provider_type": self.provider_type,
            "provider_kind": self.provider_kind,
            "auth_mode": self.auth_mode,
            "execution_mode": self.execution_mode,
            "workspace_mode": self.workspace_mode,
            "production_eligible": self.production_eligible,
            "qa_only": self.qa_only,
            "detection_classification": self.detection_classification,
            "auth_classification": self.auth_classification,
            "executable_found": self.executable_found,
            "version_detected": self.version_detected,
            "login_command": self.login_command,
            "smoke_ready": self.smoke_ready,
            "smoke_classification": self.smoke_classification,
            "installed": self.installed,
            "version": self.version,
            "executable_path": self.executable_path,
            "auth_status": self.auth_status,
            "auth_message": self.auth_message,
            "status_confidence": self.status_confidence,
            "setup_hint": self.setup_hint,
            "setup_action": self.setup_action,
            "safe_status_checked": self.safe_status_checked,
            "checked_commands": list(self.checked_commands),
            "capabilities": self.capabilities.to_dict(),
            "warnings": list(self.warnings),
            "can_run": self.can_run,
            "reason": self.reason,
            "last_checked_at": self.last_checked_at.isoformat().replace("+00:00", "Z"),
        }
