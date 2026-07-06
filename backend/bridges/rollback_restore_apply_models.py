"""Rollback restore execution result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


RollbackRestoreStatus = Literal["restored", "failed", "blocked"]
RollbackRestoreFileOperation = Literal["restore_file", "remove_created_file", "skip"]
RollbackRestoreFileStatus = Literal["success", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class RollbackRestoreFileResult:
    path: str
    operation: RollbackRestoreFileOperation
    status: RollbackRestoreFileStatus
    expected_hash: str | None
    actual_hash: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "status": self.status,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RollbackRestoreResult:
    restore_id: str
    rollback_id: str
    patch_id: str
    review_id: str
    provider_id: str
    status: RollbackRestoreStatus
    started_at: datetime
    completed_at: datetime
    files_restored: int
    files_removed: int
    files_failed: int
    restore_enabled: bool
    apply_enabled: bool
    results: tuple[RollbackRestoreFileResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "restore_id": self.restore_id,
            "rollback_id": self.rollback_id,
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "status": self.status,
            "started_at": self.started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": self.completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "files_restored": self.files_restored,
            "files_removed": self.files_removed,
            "files_failed": self.files_failed,
            "restore_enabled": self.restore_enabled,
            "apply_enabled": self.apply_enabled,
            "results": [item.to_dict() for item in self.results],
        }


def rollback_restore_result_from_dict(data: dict[str, Any]) -> RollbackRestoreResult:
    return RollbackRestoreResult(
        restore_id=str(data["restore_id"]),
        rollback_id=str(data["rollback_id"]),
        patch_id=str(data["patch_id"]),
        review_id=str(data["review_id"]),
        provider_id=str(data["provider_id"]),
        status=data.get("status", "failed"),
        started_at=datetime.fromisoformat(str(data["started_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        completed_at=datetime.fromisoformat(str(data["completed_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        files_restored=int(data.get("files_restored", 0)),
        files_removed=int(data.get("files_removed", 0)),
        files_failed=int(data.get("files_failed", 0)),
        restore_enabled=bool(data.get("restore_enabled", False)),
        apply_enabled=bool(data.get("apply_enabled", False)),
        results=tuple(
            RollbackRestoreFileResult(
                path=str(item["path"]),
                operation=item.get("operation", "skip"),
                status=item.get("status", "failed"),
                expected_hash=item.get("expected_hash"),
                actual_hash=item.get("actual_hash"),
                message=str(item.get("message", "")),
            )
            for item in list(data.get("results", []))
            if isinstance(item, dict)
        ),
    )
