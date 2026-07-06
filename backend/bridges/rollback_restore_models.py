"""Read-only rollback restore preflight models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


RollbackRestoreConflictType = Literal[
    "snapshot_missing",
    "metadata_missing",
    "backup_missing",
    "backup_modified",
    "workspace_missing",
    "workspace_hash_mismatch",
    "path_unsafe",
    "ignored_path",
    "symlink_escape",
    "current_file_changed",
    "current_file_missing",
    "created_file_changed",
    "delete_target_changed",
    "restore_unsupported",
    "unknown",
]
RollbackRestoreSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class RollbackRestoreConflict:
    path: str
    type: RollbackRestoreConflictType
    severity: RollbackRestoreSeverity
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RollbackRestorePreflightResult:
    rollback_id: str
    patch_id: str
    review_id: str
    provider_id: str
    can_restore: bool
    restore_enabled: bool
    workspace_status: str
    snapshot_status: str
    conflicts: tuple[RollbackRestoreConflict, ...] = ()
    warnings: tuple[RollbackRestoreConflict, ...] = ()
    files_to_restore: tuple[str, ...] = ()
    files_to_remove: tuple[str, ...] = ()
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "can_restore": self.can_restore,
            "restore_enabled": self.restore_enabled,
            "workspace_status": self.workspace_status,
            "snapshot_status": self.snapshot_status,
            "conflicts": [item.to_dict() for item in self.conflicts],
            "warnings": [item.to_dict() for item in self.warnings],
            "files_to_restore": list(self.files_to_restore),
            "files_to_remove": list(self.files_to_remove),
            "checked_at": self.checked_at.isoformat().replace("+00:00", "Z"),
        }
