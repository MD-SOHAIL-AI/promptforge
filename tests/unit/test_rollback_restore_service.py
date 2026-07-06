from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService, hash_file
from backend.bridges.patch_export_service import BridgePatchExportService
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.review_models import BridgeChangedFile, BridgeReviewSession
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_restore_service import RollbackRestorePreflightError, RollbackRestorePreflightService
from backend.bridges.rollback_service import RollbackSnapshotService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, RollbackSnapshotService, RollbackRestorePreflightService, BridgeAuditLog]:
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
    restore = RollbackRestorePreflightService(rollback_service=rollback, audit_log=audit)
    return reviews, exporter, rollback, restore, audit


def add_review(
    reviews: BridgeDiffService,
    workspace: Path,
    *,
    path: str = "src/main.cpp",
    change_type: str = "modified",
    before: str | None = "old\n",
    after: str | None = "new\n",
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
        status="approved",
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
        decision="approved",
    )
    reviews._sessions[review.review_id] = review
    return review


def create_snapshot(tmp_path: Path, *, change_type: str = "modified") -> tuple[Path, RollbackSnapshotService, RollbackRestorePreflightService, str]:
    reviews, exporter, rollback, restore, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(
        reviews,
        workspace,
        path="README.md" if change_type == "deleted" else "src/main.cpp",
        change_type=change_type,
        before=None if change_type == "created" else "old\n",
        after="new\n" if change_type != "deleted" else None,
    )
    patch_id = exporter.export_patch(review.review_id).patch_id
    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)
    return workspace, rollback, restore, snapshot.rollback_id


def conflict_types(result: object) -> set[str]:
    return {item["type"] for item in result.to_dict()["conflicts"]}  # type: ignore[attr-defined]


def test_restore_preflight_succeeds_for_valid_snapshot(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is True
    assert result.restore_enabled is False
    assert result.workspace_status == "ok"
    assert result.snapshot_status == "valid"
    assert result.files_to_restore == ("src/main.cpp",)
    assert result.conflicts == ()


def test_missing_rollback_snapshot_fails(tmp_path: Path) -> None:
    _, _, _, restore, _ = make_services(tmp_path)

    with pytest.raises(RollbackRestorePreflightError):
        restore.preflight_restore("missing", workspace_root=tmp_path)


def test_missing_metadata_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    (tmp_path / "patch-rollback" / rollback_id / "metadata.json").unlink()

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "metadata_missing" in conflict_types(result)


def test_missing_backup_file_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    (tmp_path / "patch-rollback" / rollback_id / "files" / "src" / "main.cpp").unlink()

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "backup_missing" in conflict_types(result)


def test_modified_backup_file_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    write(tmp_path / "patch-rollback" / rollback_id / "files" / "src" / "main.cpp", "tampered\n")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "backup_modified" in conflict_types(result)


def test_unsafe_path_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    metadata_path = tmp_path / "patch-rollback" / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../escape.cpp"
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "path_unsafe" in conflict_types(result)


def test_ignored_path_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    metadata_path = tmp_path / "patch-rollback" / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "node_modules/pkg/index.js"
    payload["files"][0]["backup_path"] = "files/node_modules/pkg/index.js"
    write(tmp_path / "patch-rollback" / rollback_id / "files" / "node_modules" / "pkg" / "index.js", "old\n")
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "ignored_path" in conflict_types(result)


def test_symlink_escape_fails(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    try:
        os.symlink(outside, workspace / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    metadata_path = tmp_path / "patch-rollback" / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "linked/new.cpp"
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "symlink_escape" in conflict_types(result)


def test_workspace_hash_mismatch_is_reported(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    other = tmp_path / "other"
    write(other / "src/main.cpp", "old\n")

    result = restore.preflight_restore(rollback_id, workspace_root=other)

    assert result.can_restore is False
    assert "workspace_hash_mismatch" in conflict_types(result)


def test_current_file_drift_is_reported_and_workspace_not_modified(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)
    write(workspace / "src/main.cpp", "changed\n")
    before = (workspace / "src/main.cpp").read_text(encoding="utf-8")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "current_file_changed" in conflict_types(result)
    assert (workspace / "src/main.cpp").read_text(encoding="utf-8") == before


def test_delete_target_drift_is_reported(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path, change_type="deleted")
    write(workspace / "README.md", "changed\n")

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is False
    assert "delete_target_changed" in conflict_types(result)


def test_audit_log_records_restore_preflight_lifecycle(tmp_path: Path) -> None:
    workspace, _, restore, rollback_id = create_snapshot(tmp_path)

    result = restore.preflight_restore(rollback_id, workspace_root=workspace)

    assert result.can_restore is True
    entries = BridgeAuditLog(tmp_path / "audit.jsonl").list_entries(200)
    assert any(entry["event"] == "rollback_restore_preflight_started" for entry in entries)
    assert any(entry["event"] == "rollback_restore_preflight_completed" for entry in entries)
    completed = [entry for entry in entries if entry["event"] == "rollback_restore_preflight_completed"][-1]
    assert completed["metadata"]["rollback_id"] == rollback_id
    assert completed["metadata"]["can_restore"] is True
