from __future__ import annotations

import json
from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    audit_events,
    export_and_verify,
    make_rig,
    write,
)


def test_apply_record_appears_in_history_and_detail_without_sensitive_content(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    rig = make_rig(tmp_path)
    write(rig.workspace / "unrelated.txt", "keep me\n")
    review = add_approved_review(rig, path="README.md", before="old readme\n", after="new readme\n")
    patch_id = export_and_verify(rig, review)

    result = rig.apply.apply_patch(patch_id, workspace_root=rig.workspace, confirmation="APPLY")
    history = rig.apply.list_applies()
    detail = rig.apply.get_apply(result.apply_id)

    assert history[0].apply_id == result.apply_id
    assert detail.apply_id == result.apply_id
    assert detail.rollback_id
    assert detail.results
    assert detail.results[0].path == "README.md"
    assert (rig.workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"

    serialized = json.dumps(detail.to_dict(), sort_keys=True)
    assert str(rig.workspace) not in serialized
    assert str(rig.workspace.resolve()) not in serialized
    assert "diff --git" not in serialized
    assert "old readme" not in serialized
    assert "new readme" not in serialized


def test_apply_audit_lifecycle_uses_hashes_not_paths_or_contents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    rig = make_rig(tmp_path)
    review = add_approved_review(rig, path="README.md", before="old readme\n", after="new readme\n")
    patch_id = export_and_verify(rig, review)

    rig.apply.apply_patch(patch_id, workspace_root=rig.workspace, confirmation="APPLY")

    entries = audit_events(rig)
    events = {entry["event"] for entry in entries}
    assert {
        "patch_apply_requested",
        "patch_apply_confirmed",
        "patch_apply_started",
        "patch_apply_preflight_passed",
        "patch_apply_rollback_snapshot_created",
        "patch_apply_staged",
        "patch_apply_completed",
    }.issubset(events)
    serialized = "\n".join(json.dumps(entry, sort_keys=True) for entry in entries)
    for forbidden in [
        str(rig.workspace),
        str(rig.workspace.resolve()),
        "old readme",
        "new readme",
        "diff --git",
        "token",
        "cookie",
        "session",
        "Google credentials",
    ]:
        assert forbidden not in serialized
