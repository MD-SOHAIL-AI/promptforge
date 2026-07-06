from __future__ import annotations

from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    assert_workspace_unchanged,
    export_and_verify,
    make_patch_old,
    make_rig,
    make_snapshot_old,
    workspace_hashes,
)


def test_patch_cleanup_removes_old_patches_only(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig)
    patch_id = export_and_verify(rig, review)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    before = workspace_hashes(rig.workspace)
    make_patch_old(rig, patch_id)

    cleanup = rig.exporter.cleanup_patches(older_than_days=30, include_missing=False)

    assert cleanup["removed"] == 1
    assert not (rig.patch_dir / patch_id).exists()
    assert review.review_id in rig.reviews._sessions
    assert (rig.rollback_dir / snapshot.rollback_id / "metadata.json").exists()
    assert rig.rollback.get_snapshot(snapshot.rollback_id).rollback_id == snapshot.rollback_id
    assert_workspace_unchanged(rig.workspace, before)


def test_rollback_cleanup_removes_old_snapshots_only(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig)
    patch_id = export_and_verify(rig, review)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    before = workspace_hashes(rig.workspace)
    make_snapshot_old(rig, snapshot.rollback_id)

    cleanup = rig.rollback.cleanup_snapshots(older_than_days=30)

    assert cleanup["removed"] == 1
    assert not (rig.rollback_dir / snapshot.rollback_id).exists()
    assert (rig.patch_dir / patch_id).exists()
    assert rig.exporter.patch_store.get(patch_id).patch_id == patch_id
    assert_workspace_unchanged(rig.workspace, before)
