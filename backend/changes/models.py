"""Provider-independent staged workspace change contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal


ChangeType = Literal["created", "modified", "deleted"]
ChangeSetStatus = Literal["pending", "applied", "undone", "discarded", "conflicted", "failed"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    path: str
    hash: str
    size: int
    mtime: str
    content: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "hash": self.hash, "size": self.size, "mtime": self.mtime}


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    workspace_root: str
    files: dict[str, SnapshotFile]
    revision: str = ""
    tree_hash: str = ""
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class ChangedFile:
    path: str
    change_type: ChangeType
    previous_hash: str | None
    new_hash: str | None
    diff_preview: str | None
    preview_supported: bool = True
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "safe": True,
            "previous_hash": self.previous_hash,
            "new_hash": self.new_hash,
            "diff_preview": self.diff_preview,
            "preview_supported": self.preview_supported,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class ChangeSet:
    change_set_id: str
    provider_id: str
    workspace_root: str
    staging_root: str
    status: ChangeSetStatus
    changed_files: tuple[ChangedFile, ...]
    summary: str
    base_revision: str = ""
    base_workspace_hash: str = ""
    staged_manifest_hash: str = ""
    authorization_id: str | None = None
    authorization_source: str = "explicit_review"
    risk_level: str = "unassessed"
    review_required: bool = True
    created_at: datetime = field(default_factory=utc_now)
    applied_at: datetime | None = None
    undone_at: datetime | None = None

    def with_status(
        self,
        status: ChangeSetStatus,
        *,
        applied_at: datetime | None = None,
        undone_at: datetime | None = None,
    ) -> "ChangeSet":
        return replace(
            self,
            status=status,
            applied_at=self.applied_at if applied_at is None else applied_at,
            undone_at=self.undone_at if undone_at is None else undone_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_id": self.change_set_id,
            "provider_id": self.provider_id,
            "workspace_root": self.workspace_root,
            "status": self.status,
            "created_at": format_datetime(self.created_at),
            "applied_at": format_datetime(self.applied_at) if self.applied_at else None,
            "undone_at": format_datetime(self.undone_at) if self.undone_at else None,
            "changed_files": [item.to_dict() for item in self.changed_files],
            "summary": self.summary,
            "base_revision": self.base_revision,
            "base_workspace_hash": self.base_workspace_hash,
            "staged_manifest_hash": self.staged_manifest_hash,
            "authorization_id": self.authorization_id,
            "authorization_source": self.authorization_source,
            "risk_level": self.risk_level,
            "review_required": self.review_required,
        }
