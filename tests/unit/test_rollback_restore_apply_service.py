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
from backend.bridges.rollback_restore_apply_service import (
    ROLLBACK_RESTORE_DISABLED_MESSAGE,
    RollbackRestoreApplyError,
    RollbackRestoreApplyService,
)
from backend.bridges.rollback_restore_service import RollbackRestorePreflightService
from backend.bridges.rollback_service import RollbackSnapshotService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, RollbackSnapshotService, RollbackRestorePreflightService, RollbackRestoreApplyService, BridgeAuditLog]:
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
    restore_preflight = RollbackRestorePreflightService(rollback_service=rollback, audit_log=audit)
    restore = RollbackRestoreApplyService(
        restore_directory=tmp_path / "rollback-restores",
        rollback_service=rollback,
        restore_preflight_service=restore_preflight,
        audit_log=audit,
    )
    return reviews, exporter, rollback, restore_preflight, restore, audit


def add_review(
    reviews: BridgeDiffService,
    workspace: Path,
    *,
    path: str = "src/main.cpp",
    change_type: str = "modified",
    before: str | None = "old\n",
    after: str | None = "new\n",
) -> BridgeReviewSession:
    workspace.mkdir(parents=True, exist_ok=True)
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
                diff_preview=f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before or ''}+{after or ''}",
            ),
        ),
        summary=f"1 {change_type}",
        decision="approved",
    )
    reviews._sessions[review.review_id] = review
    return review


def create_snapshot(tmp_path: Path, *, change_type: str = "modified") -> tuple[Path, RollbackSnapshotService, RollbackRestoreApplyService, BridgeAuditLog, str]:
    reviews, exporter, rollback, _, restore, audit = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(
        reviews,
        workspace,
        path="src/new.cpp" if change_type == "created" else "README.md" if change_type == "deleted" else "src/main.cpp",
        change_type=change_type,
        before=None if change_type == "created" else "old\n",
        after="new\n" if change_type != "deleted" else None,
    )
    patch_id = exporter.export_patch(review.review_id).patch_id
    snapshot = rollback.create_snapshot(patch_id, workspace_root=workspace)
    return workspace, rollback, restore, audit, snapshot.rollback_id


def test_restore_disabled_when_feature_flag_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)

    with pytest.raises(RollbackRestoreApplyError, match=ROLLBACK_RESTORE_DISABLED_MESSAGE):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")


def test_restore_requires_exact_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)

    with pytest.raises(RollbackRestoreApplyError, match="confirmation RESTORE"):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="restore")


def test_restore_requires_passing_preflight_missing_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)
    (tmp_path / "patch-rollback" / rollback_id / "files" / "src" / "main.cpp").unlink()

    with pytest.raises(RollbackRestoreApplyError, match="preflight failed"):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")


def test_restore_requires_passing_preflight_modified_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)
    write(tmp_path / "patch-rollback" / rollback_id / "files" / "src" / "main.cpp", "tampered\n")

    with pytest.raises(RollbackRestoreApplyError, match="preflight failed"):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")


@pytest.mark.parametrize("path", ["../escape.cpp", "node_modules/pkg/index.js"])
def test_restore_rejects_unsafe_or_ignored_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)
    metadata_path = tmp_path / "patch-rollback" / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = path
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RollbackRestoreApplyError, match="preflight failed"):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")


def test_restore_rejects_symlink_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    try:
        os.symlink(outside, workspace / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    metadata_path = tmp_path / "patch-rollback" / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "linked/main.cpp"
    metadata_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RollbackRestoreApplyError, match="preflight failed"):
        restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")


def test_modify_entry_restores_previous_file_content_and_persists_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path)
    write(workspace / "unrelated.txt", "keep\n")

    result = restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")

    assert result.status == "restored"
    assert result.files_restored == 1
    assert result.files_removed == 0
    assert result.files_failed == 0
    assert result.apply_enabled is False
    assert (workspace / "src/main.cpp").read_text(encoding="utf-8") == "old\n"
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"
    assert (tmp_path / "rollback-restores" / result.restore_id / "metadata.json").exists()
    assert restore.get_restore(result.restore_id).restore_id == result.restore_id
    assert restore.list_restores()[0].restore_id == result.restore_id


def test_delete_entry_restores_missing_file_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path, change_type="deleted")
    (workspace / "README.md").unlink()

    result = restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")

    assert result.files_restored == 1
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_create_entry_removes_created_file_only_not_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, _, rollback_id = create_snapshot(tmp_path, change_type="created")
    write(workspace / "src" / "new.cpp", "created by apply\n")
    write(workspace / "src" / "keep.cpp", "keep\n")

    result = restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")

    assert result.files_removed == 1
    assert not (workspace / "src" / "new.cpp").exists()
    assert (workspace / "src").is_dir()
    assert (workspace / "src" / "keep.cpp").read_text(encoding="utf-8") == "keep\n"


def test_restore_audit_lifecycle_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, _, restore, audit, rollback_id = create_snapshot(tmp_path)

    result = restore.restore_snapshot(rollback_id, workspace_root=workspace, confirmation="RESTORE")

    entries = audit.list_entries(300)
    events = [entry["event"] for entry in entries]
    assert "rollback_restore_requested" in events
    assert "rollback_restore_confirmed" in events
    assert "rollback_restore_started" in events
    assert "rollback_restore_completed" in events
    completed = [entry for entry in entries if entry["event"] == "rollback_restore_completed"][-1]
    assert completed["metadata"]["restore_id"] == result.restore_id
    assert completed["metadata"]["files_restored"] == 1
    assert "workspace_root_hash" in completed["metadata"]
