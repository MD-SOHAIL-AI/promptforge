"""Persistent local storage for bridge snapshots and review sessions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .review_models import (
    BridgeChangedFile,
    BridgeReviewSession,
    BridgeSnapshotFile,
    BridgeWorkspaceSnapshot,
    workspace_hash,
)


class BridgeReviewStore:
    """JSONL-backed metadata store for bridge review state.

    Snapshot records intentionally omit file content. Review records include
    capped diff previews already produced by the review service.
    """

    def __init__(self, *, snapshots_path: str | Path, reviews_path: str | Path) -> None:
        self.snapshots_path = Path(snapshots_path)
        self.reviews_path = Path(reviews_path)

    def load_snapshots(self) -> dict[str, BridgeWorkspaceSnapshot]:
        snapshots: dict[str, BridgeWorkspaceSnapshot] = {}
        for row in self._read_jsonl(self.snapshots_path):
            try:
                snapshot = snapshot_from_record(row)
            except (KeyError, TypeError, ValueError):
                continue
            snapshots[snapshot.snapshot_id] = snapshot
        return snapshots

    def load_reviews(self) -> dict[str, BridgeReviewSession]:
        reviews: dict[str, BridgeReviewSession] = {}
        for row in self._read_jsonl(self.reviews_path):
            try:
                review = review_from_record(row)
            except (KeyError, TypeError, ValueError):
                continue
            reviews[review.review_id] = review
        return reviews

    def save_snapshot(self, snapshot: BridgeWorkspaceSnapshot) -> None:
        self._append_jsonl(self.snapshots_path, snapshot_record(snapshot))

    def save_review(self, review: BridgeReviewSession) -> None:
        self._append_jsonl(self.reviews_path, review_record(review))

    def replace_reviews(self, reviews: dict[str, BridgeReviewSession]) -> None:
        self._write_jsonl(self.reviews_path, [review_record(item) for item in reviews.values()])

    def replace_snapshots(self, snapshots: dict[str, BridgeWorkspaceSnapshot]) -> None:
        self._write_jsonl(self.snapshots_path, [snapshot_record(item) for item in snapshots.values()])

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open("a", encoding="utf-8").write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        return rows


def snapshot_record(snapshot: BridgeWorkspaceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "workspace_root_hash": snapshot.workspace_root_hash or workspace_hash(snapshot.workspace_root),
        "created_at": _format_datetime(snapshot.created_at),
        "files": {
            path: {
                "path": item.path,
                "hash": item.hash,
                "size": item.size,
                "mtime": item.mtime,
            }
            for path, item in sorted(snapshot.files.items())
        },
    }


def review_record(review: BridgeReviewSession) -> dict[str, Any]:
    return {
        "review_id": review.review_id,
        "provider_id": review.provider_id,
        "workspace_root_hash": review.workspace_root_hash or workspace_hash(review.workspace_root),
        "status": review.status,
        "created_at": _format_datetime(review.created_at),
        "expires_at": _format_datetime(review.expires_at),
        "changed_files": [item.to_dict() for item in review.changed_files],
        "summary": review.summary,
        "decision": review.decision,
        "artifact_source": review.artifact_source,
        "artifact_type": review.artifact_type,
        "artifact_metadata": review.artifact_metadata,
    }


def snapshot_from_record(record: dict[str, Any]) -> BridgeWorkspaceSnapshot:
    files = {
        str(path): BridgeSnapshotFile(
            path=str(item["path"]),
            hash=str(item["hash"]),
            size=int(item["size"]),
            mtime=str(item["mtime"]),
            content=None,
        )
        for path, item in dict(record["files"]).items()
        if isinstance(item, dict)
    }
    return BridgeWorkspaceSnapshot(
        workspace_root="",
        files=files,
        snapshot_id=str(record["snapshot_id"]),
        created_at=_parse_datetime(str(record["created_at"])),
        workspace_root_hash=str(record["workspace_root_hash"]),
    )


def review_from_record(record: dict[str, Any]) -> BridgeReviewSession:
    changed_files = tuple(
        BridgeChangedFile(
            path=str(item["path"]),
            change_type=item["change_type"],
            safe=bool(item["safe"]),
            previous_hash=item.get("previous_hash"),
            new_hash=item.get("new_hash"),
            diff_preview=item.get("diff_preview"),
            preview_supported=bool(item.get("preview_supported", True)),
            warning=item.get("warning"),
        )
        for item in list(record.get("changed_files", []))
        if isinstance(item, dict)
    )
    return BridgeReviewSession(
        review_id=str(record["review_id"]),
        provider_id=str(record["provider_id"]),
        workspace_root="",
        workspace_root_hash=str(record["workspace_root_hash"]),
        status=record["status"],
        created_at=_parse_datetime(str(record["created_at"])),
        expires_at=_parse_datetime(str(record["expires_at"])),
        changed_files=changed_files,
        summary=str(record.get("summary", "")),
        decision=record.get("decision"),
        artifact_source=record.get("artifact_source"),
        artifact_type=record.get("artifact_type"),
        artifact_metadata=record.get("artifact_metadata"),
    )


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
