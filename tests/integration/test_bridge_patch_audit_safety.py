from __future__ import annotations

import json
from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    audit_events,
    export_and_verify,
    make_rig,
    workspace_hashes,
)


def test_bridge_patch_audit_flow_uses_hashes_not_raw_paths_or_contents(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    review = add_approved_review(rig)
    before = workspace_hashes(rig.workspace)
    patch_id = export_and_verify(rig, review)
    preflight = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)
    snapshot = rig.rollback.create_snapshot(patch_id, workspace_root=rig.workspace)
    restore = rig.restore_preflight.preflight_restore(snapshot.rollback_id, workspace_root=rig.workspace)

    assert preflight.apply_enabled is False
    assert restore.restore_enabled is False
    assert workspace_hashes(rig.workspace) == before

    entries = audit_events(rig)
    events = {entry["event"] for entry in entries}
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

    serialized = "\n".join(json.dumps(entry, sort_keys=True) for entry in entries)
    forbidden_values = [
        str(rig.workspace),
        str(rig.workspace.resolve()),
        "old readme",
        "new readme",
        "token",
        "cookie",
        "session",
        "Google credentials",
        "diff --git",
    ]
    for value in forbidden_values:
        assert value not in serialized
