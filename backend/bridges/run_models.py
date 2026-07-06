"""Bridge sandbox run data contracts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


BridgeSandboxRunStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
    "review_ready",
    "completed_no_changes",
    "failed_timeout",
]


@dataclass(slots=True)
class BridgeRunDiagnostics:
    sandbox_entered: bool = False
    working_directory_identity: Literal["sandbox", "unknown"] = "unknown"
    instruction_delivered: bool = False
    instruction_length: int = 0
    instruction_sha256: str = ""
    exit_code_classification: Literal["not_started", "zero", "nonzero", "timeout", "cancelled", "start_failed"] = "not_started"
    provider_output_classification: Literal[
        "not_observed",
        "empty",
        "auth_required",
        "permission_or_trust_required",
        "invalid_invocation",
        "error",
        "nonempty",
    ] = "not_observed"
    sandbox_marker_exists: bool = False
    pre_run_file_count: int = 0
    post_run_file_count: int = 0
    diff_changed_file_count: int = 0
    ignored_changed_file_count: int = 0
    review_changed_file_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_entered": self.sandbox_entered,
            "working_directory_identity": self.working_directory_identity,
            "instruction_delivered": self.instruction_delivered,
            "instruction_length": self.instruction_length,
            "instruction_sha256": self.instruction_sha256,
            "exit_code_classification": self.exit_code_classification,
            "provider_output_classification": self.provider_output_classification,
            "sandbox_marker_exists": self.sandbox_marker_exists,
            "pre_run_file_count": self.pre_run_file_count,
            "post_run_file_count": self.post_run_file_count,
            "diff_changed_file_count": self.diff_changed_file_count,
            "ignored_changed_file_count": self.ignored_changed_file_count,
            "review_changed_file_count": self.review_changed_file_count,
        }


@dataclass(slots=True)
class BridgeSandboxRun:
    provider_id: str
    workspace_root_hash: str
    sandbox_root: str
    status: BridgeSandboxRunStatus = "pending"
    run_id: str = field(default_factory=lambda: f"bridge-run-{uuid.uuid4().hex}")
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    exit_code: int | None = None
    stdout_preview: str | None = None
    stderr_preview: str | None = None
    changed_file_count: int = 0
    review_id: str | None = None
    error_message: str | None = None
    diagnostics: BridgeRunDiagnostics = field(default_factory=BridgeRunDiagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "workspace_root_hash": self.workspace_root_hash,
            "sandbox_root": self.sandbox_root,
            "status": self.status,
            "started_at": self.started_at.isoformat().replace("+00:00", "Z"),
            "completed_at": self.completed_at.isoformat().replace("+00:00", "Z") if self.completed_at else None,
            "exit_code": self.exit_code,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "changed_file_count": self.changed_file_count,
            "review_id": self.review_id,
            "error_message": self.error_message,
            "diagnostics": self.diagnostics.to_dict(),
        }
