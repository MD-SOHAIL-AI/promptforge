from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService, hash_file
from backend.bridges.patch_export_service import BridgePatchExportService, sha256_file
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.patch_store import BridgePatchRecord
from backend.bridges.review_models import BridgeChangedFile, BridgeReviewSession
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_service import RollbackSnapshotError, RollbackSnapshotService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, RollbackSnapshotService, BridgeAuditLog]:
    audit = BridgeAuditLog(tmp_path / "audit.jsonl")
    reviews = BridgeDiffService(
        audit_log=audit,
        store=BridgeReviewStore(
            snapshots_path=tmp_path / "snapshots.jsonl",
            reviews_path=tmp_path / "reviews.jsonl",
        ),
    )
    exporter = BridgePatchExportService(
        review_service=reviews,
        patch_directory=tmp_path / "patches",
        audit_log=audit,
    )
    preflight = PatchPreflightService(
        patch_store=exporter.patch_store,
        review_service=reviews,
        audit_log=audit,
    )
    rollback = RollbackSnapshotService(
        rollback_directory=tmp_path / "patch-rollback",
        preflight_service=preflight,
        audit_log=audit,
    )
    return reviews, exporter, rollback, audit


def add_review(
    reviews: BridgeDiffService,
    workspace: Path,
    *,
    path: str = "src/main.cpp",
    change_type: str = "modified",
    before: str | None = "old\n",
    after: str | None = "new\n",
    status: str = "approved",
) -> BridgeReviewSession:
    if before is not None:
        write(workspace / path, before)
    previous_hash = hash_file(workspace / path) if before is not None and (workspace / path).exists() else None
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id=f"review-{path.replace('/', '-').replace('.', '-')}-{change_type}",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(workspace),
        workspace_root_hash=hash_workspace_root(str(workspace.resolve())),
        status=status,  # type: ignore[arg-type]
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(
            BridgeChangedFile(
                path=path,
                change_type=change_type,  # type: ignore[arg-type]
                safe=True,
                previous_hash=previous_hash,
                new_hash="new-hash" if after is not None else None,
                diff_preview=f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before or ''}+{after or ''}",
            ),
        ),
        summary=f"1 {change_type}",
        decision="approved" if status == "approved" else None,
    )
    reviews._sessions[review.review_id] = review
    return review


def export_review(exporter: BridgePatchExportService, review: BridgeReviewSession) -> str:
    return exporter.export_patch(review.review_id).patch_id


def test_snapshot_requires_valid_preflight_and_backs_up_modified_file(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    before = (workspace / "src/main.cpp").read_text(encoding="utf-8")

    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)

    assert snapshot.status == "created"
    assert snapshot.restore_enabled is False
    assert snapshot.apply_id is None
    assert len(snapshot.files) == 1
    backup = snapshot.files[0]
    assert backup.path == "src/main.cpp"
    assert backup.change_type == "modify"
    assert backup.existed_before is True
    assert backup.previous_hash == hash_file(workspace / "src/main.cpp")
    assert backup.backup_path == "files/src/main.cpp"
    assert (tmp_path / "patch-rollback" / snapshot.rollback_id / backup.backup_path).read_text(encoding="utf-8") == before
    assert (workspace / "src/main.cpp").read_text(encoding="utf-8") == before


def test_snapshot_rejects_modified_patch(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    patch_path = tmp_path / "patches" / patch_id
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    with pytest.raises(RollbackSnapshotError):
        rollback.create_snapshot(patch_id, workspace_root=workspace)


def test_snapshot_rejects_unapproved_review(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, status="pending")
    patch_id = export_review(exporter, review)

    with pytest.raises(RollbackSnapshotError):
        rollback.create_snapshot(patch_id, workspace_root=workspace)


def test_snapshot_rejects_unsafe_path(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="review-unsafe",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(workspace),
        workspace_root_hash=hash_workspace_root(str(workspace.resolve())),
        status="approved",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(BridgeChangedFile(path="../escape.cpp", change_type="created", safe=False),),
        summary="1 unsafe",
        decision="approved",
    )
    reviews._sessions[review.review_id] = review
    patch_path = tmp_path / "patches" / "unsafe.patch"
    write(patch_path, "diff --git a/../escape.cpp b/../escape.cpp\n")
    exporter.patch_store.upsert(
        BridgePatchRecord(
            patch_id="unsafe.patch",
            review_id=review.review_id,
            provider_id=review.provider_id,
            created_at=now,
            patch_path="unsafe.patch",
            metadata_path="unsafe.metadata.json",
            patch_size=patch_path.stat().st_size,
            patch_sha256=sha256_file(patch_path),
            integrity_status="valid",
            changed_file_count=1,
            created_files=("../escape.cpp",),
            modified_files=(),
            deleted_files=(),
            workspace_root_hash=review.workspace_root_hash or "",
            review_status_at_export="approved",
            apply_enabled=False,
        )
    )

    with pytest.raises(RollbackSnapshotError):
        rollback.create_snapshot("unsafe.patch", workspace_root=workspace)


def test_snapshot_backs_up_delete_target(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, path="README.md", change_type="deleted", before="delete me\n", after=None)
    patch_id = export_review(exporter, review)

    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)

    assert snapshot.files[0].change_type == "delete"
    assert (tmp_path / "patch-rollback" / snapshot.rollback_id / "files" / "README.md").read_text(encoding="utf-8") == "delete me\n"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "delete me\n"


def test_snapshot_records_create_target_without_backup_for_missing_file(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    review = add_review(reviews, workspace, path="src/new.cpp", change_type="created", before=None, after="new\n")
    patch_id = export_review(exporter, review)

    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)

    assert snapshot.files[0].change_type == "create"
    assert snapshot.files[0].existed_before is False
    assert snapshot.files[0].backup_path is None
    assert not (tmp_path / "patch-rollback" / snapshot.rollback_id / "files" / "src" / "new.cpp").exists()


def test_snapshot_does_not_follow_symlink_escape(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    workspace.mkdir(parents=True)
    try:
        os.symlink(outside, workspace / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    review = add_review(reviews, workspace, path="linked/new.cpp", change_type="created", before=None, after="new\n")
    patch_id = export_review(exporter, review)

    with pytest.raises(RollbackSnapshotError):
        rollback.create_snapshot(patch_id, workspace_root=workspace)


def test_snapshot_metadata_uses_relative_paths_only(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)

    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)
    metadata = (tmp_path / "patch-rollback" / snapshot.rollback_id / "metadata.json").read_text(encoding="utf-8")

    assert str(workspace) not in metadata
    assert "files/src/main.cpp" in metadata
    assert snapshot.workspace_root_hash == hash_workspace_root(str(workspace.resolve()))


def test_snapshot_list_delete_and_cleanup(tmp_path: Path) -> None:
    reviews, exporter, rollback, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    old_snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)
    recent_snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)

    old_metadata_path = tmp_path / "patch-rollback" / old_snapshot.rollback_id / "metadata.json"
    old_payload = json.loads(old_metadata_path.read_text(encoding="utf-8"))
    old_payload["created_at"] = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat().replace("+00:00", "Z")
    old_metadata_path.write_text(json.dumps(old_payload, ensure_ascii=True, indent=2), encoding="utf-8")

    listed = rollback.list_snapshots()
    assert {item.rollback_id for item in listed} == {old_snapshot.rollback_id, recent_snapshot.rollback_id}

    cleanup = rollback.cleanup_snapshots(older_than_days=30)
    assert cleanup["removed"] == 1
    assert not (tmp_path / "patch-rollback" / old_snapshot.rollback_id).exists()
    assert (tmp_path / "patch-rollback" / recent_snapshot.rollback_id).exists()

    deleted = rollback.delete_snapshot(recent_snapshot.rollback_id)
    assert deleted.rollback_id == recent_snapshot.rollback_id
    assert rollback.list_snapshots() == []


def test_audit_log_records_rollback_snapshot_lifecycle(tmp_path: Path) -> None:
    reviews, exporter, rollback, audit = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)

    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)
    rollback.delete_snapshot(snapshot.rollback_id)
    rollback.cleanup_snapshots(older_than_days=30)

    entries = audit.list_entries(200)
    assert any(entry["event"] == "rollback_snapshot_requested" for entry in entries)
    assert any(entry["event"] == "rollback_snapshot_created" for entry in entries)
    assert any(entry["event"] == "rollback_snapshot_deleted" for entry in entries)
    assert any(entry["event"] == "rollback_snapshot_cleanup_started" for entry in entries)
    assert any(entry["event"] == "rollback_snapshot_cleanup_completed" for entry in entries)
    created = [entry for entry in entries if entry["event"] == "rollback_snapshot_created"][-1]
    assert created["metadata"]["patch_id"] == patch_id
    assert created["metadata"]["files_backed_up"] == 1
