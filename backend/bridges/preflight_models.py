"""Read-only patch preflight result models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


PatchPreflightConflictType = Literal[
    "clean",
    "workspace_drift",
    "patch_modified",
    "patch_missing",
    "review_missing",
    "review_not_approved",
    "review_expired",
    "provider_mismatch",
    "path_unsafe",
    "target_missing",
    "target_changed",
    "delete_conflict",
    "binary_unsupported",
    "large_file_unsupported",
    "ignored_path",
    "parse_error",
    "unknown",
]
PatchPreflightSeverity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class PatchPreflightConflict:
    path: str
    type: PatchPreflightConflictType
    severity: PatchPreflightSeverity
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PatchPreflightResult:
    patch_id: str
    review_id: str
    provider_id: str
    can_apply: bool
    apply_enabled: bool
    integrity_status: str
    review_status: str
    workspace_status: str
    conflicts: tuple[PatchPreflightConflict, ...] = ()
    warnings: tuple[PatchPreflightConflict, ...] = ()
    files_to_create: tuple[str, ...] = ()
    files_to_modify: tuple[str, ...] = ()
    files_to_delete: tuple[str, ...] = ()
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "can_apply": self.can_apply,
            "apply_enabled": self.apply_enabled,
            "integrity_status": self.integrity_status,
            "review_status": self.review_status,
            "workspace_status": self.workspace_status,
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": [item.to_dict() for item in self.warnings],
            "files_to_create": list(self.files_to_create),
            "files_to_modify": list(self.files_to_modify),
            "files_to_delete": list(self.files_to_delete),
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
        }
