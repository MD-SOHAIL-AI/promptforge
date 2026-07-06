from __future__ import annotations

from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    audit_events,
    export_and_verify,
    make_rig,
    workspace_hashes,
    write,
)


def test_feature_flagged_rollback_restore_flow_restores_only_snapshot_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    rig = make_rig(tmp_path)
    review = add_approved_review(rig, path="README.md", before="old readme\n", after="new readme\n")
    patch_id = export_and_verify(rig, review)
    preflight = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    restore_preflight = rig.restore_preflight.preflight_restore(snapshot.rollback_id, workspace_root=rig.workspace)
    write(rig.workspace / "unrelated.txt", "keep me\n")
    before = workspace_hashes(rig.workspace)

    result = rig.restore_apply.restore_snapshot(snapshot.rollback_id, workspace_root=rig.workspace, confirmation="RESTORE")
    after = workspace_hashes(rig.workspace)

    assert preflight.can_apply is True
    assert preflight.apply_enabled is False
    assert restore_preflight.can_restore is True
    assert result.status == "restored"
    assert result.restore_enabled is True
    assert result.apply_enabled is False
    assert result.files_restored == 1
    assert result.files_removed == 0
    assert result.files_failed == 0
    assert (rig.workspace / "README.md").read_text(encoding="utf-8") == "old readme\n"
    assert (rig.workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"
    changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    assert changed == set()

    events = {entry["event"] for entry in audit_events(rig)}
    assert "rollback_restore_requested" in events
    assert "rollback_restore_confirmed" in events
    assert "rollback_restore_started" in events
    assert "rollback_restore_completed" in events
