"""Rollback snapshot metadata models for future patch apply."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal


RollbackSnapshotStatus = Literal["created", "deleted"]
RollbackChangeType = Literal["create", "modify", "delete"]


@dataclass(frozen=True, slots=True)
class RollbackFileBackup:
    path: str
    change_type: RollbackChangeType
    existed_before: bool
    previous_hash: str | None
    backup_path: str | None
    size: int
    mtime: str | None
    post_apply_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "existed_before": self.existed_before,
            "previous_hash": self.previous_hash,
            "backup_path": self.backup_path,
            "size": self.size,
            "mtime": self.mtime,
            "post_apply_hash": self.post_apply_hash,
        }


@dataclass(frozen=True, slots=True)
class RollbackSnapshot:
    rollback_id: str
    patch_id: str
    review_id: str
    provider_id: str
    workspace_root_hash: str
    created_at: datetime
    status: RollbackSnapshotStatus
    files: tuple[RollbackFileBackup, ...]
    total_bytes: int
    apply_id: str | None = None
    restore_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rollback_id": self.rollback_id,
            "patch_id": self.patch_id,
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "workspace_root_hash": self.workspace_root_hash,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "status": self.status,
            "files": [item.to_dict() for item in self.files],
            "total_bytes": self.total_bytes,
            "apply_id": self.apply_id,
            "restore_enabled": self.restore_enabled,
        }


def rollback_snapshot_from_dict(data: dict[str, Any]) -> RollbackSnapshot:
    return RollbackSnapshot(
        rollback_id=str(data["rollback_id"]),
        patch_id=str(data["patch_id"]),
        review_id=str(data["review_id"]),
        provider_id=str(data["provider_id"]),
        workspace_root_hash=str(data.get("workspace_root_hash", "")),
        created_at=datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        status=data.get("status", "created"),
        files=tuple(
            RollbackFileBackup(
                path=str(item["path"]),
                change_type=item["change_type"],
                existed_before=bool(item.get("existed_before", False)),
                previous_hash=item.get("previous_hash"),
                backup_path=item.get("backup_path"),
                size=int(item.get("size", 0)),
                mtime=item.get("mtime"),
                post_apply_hash=item.get("post_apply_hash"),
            )
            for item in list(data.get("files", []))
            if isinstance(item, dict)
        ),
        total_bytes=int(data.get("total_bytes", 0)),
        apply_id=data.get("apply_id"),
        restore_enabled=bool(data.get("restore_enabled", False)),
    )
