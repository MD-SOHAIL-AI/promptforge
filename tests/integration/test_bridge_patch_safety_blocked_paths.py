from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.rollback_service import RollbackSnapshotError
from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    add_review,
    assert_workspace_unchanged,
    conflict_types,
    export_and_verify,
    make_manual_patch_record,
    make_rig,
    workspace_hashes,
    write,
)


def test_unapproved_review_blocks_preflight_and_snapshot(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_review(rig, status="pending")
    patch_id = rig.exporter.export_patch(review.review_id).patch_id
    before = workspace_hashes(rig.workspace)

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.can_apply is False
    assert "review_not_approved" in conflict_types(result)
    with pytest.raises(RollbackSnapshotError):
        rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    assert_workspace_unchanged(rig.workspace, before)


def test_modified_patch_blocks_preflight_and_snapshot(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig)
    patch_id = export_and_verify(rig, review)
    before = workspace_hashes(rig.workspace)
    patch_path = rig.patch_dir / patch_id
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.integrity_status == "modified"
    assert result.can_apply is False
    assert "patch_modified" in conflict_types(result)
    with pytest.raises(RollbackSnapshotError):
        rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    assert_workspace_unchanged(rig.workspace, before)


def test_workspace_drift_blocks_preflight_and_snapshot_without_extra_writes(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig, path="src/main.cpp", before="void setup() {}\n", after="void setup() { pinMode(2, OUTPUT); }\n")
    patch_id = export_and_verify(rig, review)
    write(rig.workspace / "src" / "main.cpp", "void setup() { /* user changed */ }\n")
    before = workspace_hashes(rig.workspace)

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.can_apply is False
    assert "target_changed" in conflict_types(result) or "workspace_drift" in conflict_types(result)
    with pytest.raises(RollbackSnapshotError):
        rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    assert_workspace_unchanged(rig.workspace, before)


def test_missing_backup_blocks_restore_preflight(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig)
    patch_id = export_and_verify(rig, review)
    before = workspace_hashes(rig.workspace)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    backup_path = rig.rollback_dir / snapshot.rollback_id / "files" / "README.md"
    backup_path.unlink()

    restore = rig.restore_preflight.preflight_restore(snapshot.rollback_id, workspace_root=rig.workspace)

    assert restore.can_restore is False
    assert restore.restore_enabled is False
    assert "backup_missing" in conflict_types(restore)
    assert_workspace_unchanged(rig.workspace, before)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("../evil.txt", "path_unsafe"),
        (".git/config", "ignored_path"),
        (".pio/build/file", "ignored_path"),
        ("node_modules/pkg/index.js", "ignored_path"),
    ],
)
def test_unsafe_or_ignored_patch_paths_block_preflight_and_snapshot(tmp_path: Path, path: str, expected: str) -> None:
    rig = make_rig(tmp_path)
    patch_id = make_manual_patch_record(rig, patch_id=f"unsafe-{expected}-{len(path)}.patch", path=path, safe=expected != "path_unsafe")
    before = workspace_hashes(rig.workspace)

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.can_apply is False
    assert expected in conflict_types(result)
    with pytest.raises(RollbackSnapshotError):
        rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    assert list(rig.rollback.list_snapshots()) == []
    assert_workspace_unchanged(rig.workspace, before)
