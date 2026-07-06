from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffError, BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.review_models import BridgeReviewSession


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def store(tmp_path: Path) -> BridgeReviewStore:
    return BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "bridge-snapshots.jsonl",
        reviews_path=tmp_path / "state" / "bridge-reviews.jsonl",
    )


def service(tmp_path: Path) -> BridgeDiffService:
    return BridgeDiffService(
        audit_log=BridgeAuditLog(tmp_path / "state" / "bridge-audit.jsonl"),
        store=store(tmp_path),
    )


def test_snapshot_and_review_persist_after_store_reload(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")
    reviews = service(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "new\n")
    review = reviews.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)

    restored = service(tmp_path)

    assert restored.get_snapshot(snapshot.snapshot_id).snapshot_id == snapshot.snapshot_id
    assert restored.get_review(review.review_id).review_id == review.review_id


def test_approved_and_rejected_reviews_survive_reload(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")
    reviews = service(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "new\n")
    approved = reviews.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)
    reviews.approve_review(approved.review_id)

    snapshot_2 = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "newer\n")
    rejected = reviews.create_review(provider_id="claude_code_bridge", workspace_root=workspace, snapshot=snapshot_2)
    reviews.reject_review(rejected.review_id)

    restored = service(tmp_path)

    assert restored.get_review(approved.review_id).status == "approved"
    assert restored.get_review(approved.review_id).decision == "approved"
    assert restored.get_review(rejected.review_id).status == "rejected"
    assert restored.get_review(rejected.review_id).decision == "rejected"


def test_expired_review_cannot_be_approved_and_cleanup_only_removes_expired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")
    reviews = service(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "new\n")
    expired = reviews.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)
    reviews._sessions[expired.review_id] = reviews._replace_status(
        expired,
        "pending",
    )
    reviews._sessions[expired.review_id] = type(expired)(
        review_id=expired.review_id,
        provider_id=expired.provider_id,
        workspace_root=expired.workspace_root,
        workspace_root_hash=expired.workspace_root_hash,
        status="pending",
        created_at=expired.created_at - timedelta(days=2),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        changed_files=expired.changed_files,
        summary=expired.summary,
        decision=None,
    )
    reviews.store.save_review(reviews._sessions[expired.review_id])  # type: ignore[union-attr]

    snapshot_2 = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "newer\n")
    pending = reviews.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot_2)

    with pytest.raises(BridgeDiffError):
        reviews.approve_review(expired.review_id)

    result = reviews.cleanup_expired_reviews()

    assert result["removed"] == 1
    assert reviews.get_review(pending.review_id).status == "pending"
    with pytest.raises(BridgeDiffError):
        reviews.get_review(expired.review_id)


def test_store_uses_workspace_hash_not_raw_path(tmp_path: Path) -> None:
    workspace = tmp_path / "secret-workspace"
    write(workspace / "src" / "main.cpp", "old\n")
    reviews = service(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "new\n")
    reviews.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)

    raw_snapshots = (tmp_path / "state" / "bridge-snapshots.jsonl").read_text(encoding="utf-8")
    raw_reviews = (tmp_path / "state" / "bridge-reviews.jsonl").read_text(encoding="utf-8")

    assert str(workspace) not in raw_snapshots
    assert str(workspace) not in raw_reviews
    assert "secret-workspace" not in raw_snapshots
    assert "secret-workspace" not in raw_reviews
    assert hash_workspace_root(str(workspace)) in raw_snapshots
    assert hash_workspace_root(str(workspace)) in raw_reviews


def test_scratch_smoke_review_metadata_survives_reload_without_workspace_diff(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="bridge-review-scratch",
        provider_id="agy",
        workspace_root="",
        workspace_root_hash="a" * 64,
        status="pending",
        created_at=now,
        expires_at=now + timedelta(days=1),
        changed_files=(),
        summary="AGY scratch smoke artifact imported for review; no apply authority.",
        artifact_source="agy_scratch",
        artifact_type="scratch_smoke",
        artifact_metadata={
            "filename": "FORGEX_AGY_SCRATCH_SMOKE_run_nonce.txt",
            "content_hash": "b" * 64,
            "size_bytes": 100,
            "classification": "SCRATCH_IMPORT_PASS",
        },
    )
    target = store(tmp_path)
    target.save_review(review)

    restored = target.load_reviews()[review.review_id]

    assert restored.changed_files == ()
    assert restored.artifact_source == "agy_scratch"
    assert restored.artifact_type == "scratch_smoke"
    assert restored.artifact_metadata == review.artifact_metadata
