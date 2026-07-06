"""Patch apply result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


PatchApplyStatus = Literal[
    "blocked",
    "staged",
    "applying",
    "applied",
    "failed",
    "failed_rolled_back",
    "failed_rollback_failed",
]
PatchApplyOperation = Literal["create", "modify", "delete"]
PatchApplyFileStatus = Literal["success", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class PatchApplyFileResult:
    path: str
    operation: PatchApplyOperation
    status: PatchApplyFileStatus
    before_hash: str | None
    after_hash: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "operation": self.operation,
            "status": self.status,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PatchApplyResult:
    apply_id: str
    patch_id: str
    review_id: str
    provider_id: str
    rollback_id: str | None
    workspace_root_hash: str
    status: PatchApplyStatus
    started_at: datetime
    completed_at: datetime
    files_created: int
    files_modified: int
    files_deleted: int
    files_failed: int
    rollback_available: bool
    apply_enabled: bool
    restore_enabled: bool
    results: tuple[PatchApplyFileResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "apply_id": self.apply_id,
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "rollback_id": self.rollback_id,
            "workspace_root_hash": self.workspace_root_hash,
            "status": self.status,
            "started_at": self.started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "completed_at": self.completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "files_deleted": self.files_deleted,
            "files_failed": self.files_failed,
            "rollback_available": self.rollback_available,
            "apply_enabled": self.apply_enabled,
            "restore_enabled": self.restore_enabled,
            "results": [item.to_dict() for item in self.results],
        }


def patch_apply_result_from_dict(data: dict[str, Any]) -> PatchApplyResult:
    return PatchApplyResult(
        apply_id=str(data["apply_id"]),
        patch_id=str(data["patch_id"]),
        review_id=str(data["review_id"]),
        provider_id=str(data["provider_id"]),
        rollback_id=data.get("rollback_id"),
        workspace_root_hash=str(data.get("workspace_root_hash", "")),
        status=data.get("status", "failed"),
        started_at=datetime.fromisoformat(str(data["started_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        completed_at=datetime.fromisoformat(str(data["completed_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        files_created=int(data.get("files_created", 0)),
        files_modified=int(data.get("files_modified", 0)),
        files_deleted=int(data.get("files_deleted", 0)),
        files_failed=int(data.get("files_failed", 0)),
        rollback_available=bool(data.get("rollback_available", False)),
        apply_enabled=bool(data.get("apply_enabled", False)),
        restore_enabled=bool(data.get("restore_enabled", False)),
        results=tuple(
            PatchApplyFileResult(
                path=str(item["path"]),
                operation=item.get("operation", "modify"),
                status=item.get("status", "failed"),
                before_hash=item.get("before_hash"),
                after_hash=item.get("after_hash"),
                message=str(item.get("message", "")),
            )
            for item in list(data.get("results", data.get("file_results", [])))
            if isinstance(item, dict)
        ),
    )
