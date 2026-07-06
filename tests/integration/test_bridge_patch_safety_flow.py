from __future__ import annotations

from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    assert_workspace_unchanged,
    audit_events,
    export_and_verify,
    make_rig,
    workspace_hashes,
)


def test_bridge_patch_safety_happy_path_keeps_workspace_unchanged(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig, path="README.md", before="old readme\n", after="new readme\n")
    before = workspace_hashes(rig.workspace)

    patch_id = export_and_verify(rig, review)
    preflight = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    restore = rig.restore_preflight.preflight_restore(snapshot.rollback_id, workspace_root=rig.workspace)

    assert preflight.can_apply is True
    assert preflight.apply_enabled is False
    assert preflight.files_to_modify == ("README.md",)
    assert snapshot.restore_enabled is False
    assert len(snapshot.files) == 1
    assert (rig.rollback_dir / snapshot.rollback_id / "files" / "README.md").exists()
    assert restore.can_restore is True
    assert restore.restore_enabled is False
    assert restore.files_to_restore == ("README.md",)
    assert_workspace_unchanged(rig.workspace, before)

    events = {entry["event"] for entry in audit_events(rig)}
    assert {
        "review_created",
        "review_approved",
        "bridge_patch_exported",
        "bridge_patch_verified",
        "patch_preflight_started",
        "patch_preflight_completed",
        "rollback_snapshot_requested",
        "rollback_snapshot_created",
        "rollback_restore_preflight_started",
        "rollback_restore_preflight_completed",
    }.issubset(events)
