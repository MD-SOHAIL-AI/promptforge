"""Bridge review and diff data contracts."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


BridgeReviewStatus = Literal["pending", "approved", "rejected", "expired"]
BridgeChangeType = Literal["created", "modified", "deleted"]
BridgeDecision = Literal["approved", "rejected"]
BridgeArtifactSource = Literal["agy_scratch", "forgex_tool_runtime", "codex_exec", "manual_agy_scratch_folder", "expected_agy_scratch_folder"]
BridgeArtifactType = Literal["scratch_smoke", "tool_runtime_diff", "codex_subscription_bridge_diff", "codex_oauth_bridge_smoke_diff", "scratch_project_import"]


def workspace_hash(workspace_root: str) -> str:
    return hashlib.sha256(workspace_root.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BridgeSnapshotFile:
    path: str
    hash: str
    size: int
    mtime: str
    content: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "hash": self.hash,
            "size": self.size,
            "mtime": self.mtime,
        }


@dataclass(frozen=True, slots=True)
class BridgeWorkspaceSnapshot:
    workspace_root: str
    files: dict[str, BridgeSnapshotFile]
    snapshot_id: str = field(default_factory=lambda: f"bridge-snapshot-{uuid.uuid4().hex}")
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    workspace_root_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "workspace_root": self.workspace_root,
            "workspace_root_hash": self.workspace_root_hash or workspace_hash(self.workspace_root),
            "files": {
                path: item.to_dict()
                for path, item in sorted(self.files.items())
            },
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class BridgeChangedFile:
    path: str
    change_type: BridgeChangeType
    safe: bool
    previous_hash: str | None = None
    new_hash: str | None = None
    diff_preview: str | None = None
    preview_supported: bool = True
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "safe": self.safe,
            "previous_hash": self.previous_hash,
            "new_hash": self.new_hash,
            "diff_preview": self.diff_preview,
            "preview_supported": self.preview_supported,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class BridgeFileDiff:
    path: str
    change_type: BridgeChangeType
    diff_preview: str | None
    preview_supported: bool
    capped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "diff_preview": self.diff_preview,
            "preview_supported": self.preview_supported,
            "capped": self.capped,
        }


@dataclass(frozen=True, slots=True)
class BridgeReviewSession:
    review_id: str
    provider_id: str
    workspace_root: str
    status: BridgeReviewStatus
    created_at: datetime
    expires_at: datetime
    changed_files: tuple[BridgeChangedFile, ...]
    summary: str
    workspace_root_hash: str | None = None
    decision: BridgeDecision | None = None
    artifact_source: BridgeArtifactSource | None = None
    artifact_type: BridgeArtifactType | None = None
    artifact_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "workspace_root": self.workspace_root,
            "workspace_root_hash": self.workspace_root_hash or workspace_hash(self.workspace_root),
            "status": self.status,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "changed_files": [item.to_dict() for item in self.changed_files],
            "summary": self.summary,
            "decision": self.decision,
            "artifact_source": self.artifact_source,
            "artifact_type": self.artifact_type,
            "artifact_metadata": self.artifact_metadata,
        }


@dataclass(frozen=True, slots=True)
class BridgeApprovalDecision:
    review_id: str
    decision: BridgeDecision
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "decision": self.decision,
            "decided_at": self.decided_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class BridgeAuditEntry:
    event: str
    provider_id: str
    workspace_root_hash: str
    changed_file_count: int
    approved: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    review_id: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "provider_id": self.provider_id,
            "workspace_root_hash": self.workspace_root_hash,
            "changed_file_count": self.changed_file_count,
            "approved": self.approved,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "review_id": self.review_id,
            "metadata": self.metadata or {},
        }
