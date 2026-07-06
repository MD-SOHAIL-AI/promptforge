from __future__ import annotations

from pathlib import Path

from tests.integration.bridge_patch_safety_helpers import (
    add_approved_review,
    audit_events,
    export_and_verify,
    make_rig,
    write,
    workspace_hashes,
)


def apply_change(tmp_path: Path, monkeypatch, *, path: str, before: str | None, after: str | None):
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    rig = make_rig(tmp_path)
    write(rig.workspace / "unrelated.txt", "keep me\n")
    change_type = "created" if before is None else "deleted" if after is None else "modified"
    review = add_approved_review(rig, path=path, change_type=change_type, before=before, after=after)
    patch_id = export_and_verify(rig, review)
    result = rig.apply.apply_patch(patch_id, workspace_root=rig.workspace, confirmation="APPLY")
    return rig, result


def restore_apply(rig, result):
    restore_preflight = rig.restore_preflight.preflight_restore(result.rollback_id, workspace_root=rig.workspace)
    assert restore_preflight.can_restore is True
    restore = rig.restore_apply.restore_snapshot(result.rollback_id, workspace_root=rig.workspace, confirmation="RESTORE")
    return restore


def test_restore_can_restore_applied_modify_patch_and_preserve_unrelated_files(tmp_path: Path, monkeypatch) -> None:
    rig, result = apply_change(tmp_path, monkeypatch, path="README.md", before="old readme\n", after="new readme\n")
    assert (rig.workspace / "README.md").read_text(encoding="utf-8") == "new readme\n"
    before_restore_hashes = workspace_hashes(rig.workspace)

    restore = restore_apply(rig, result)

    assert restore.status == "restored"
    assert restore.files_restored == 1
    assert (rig.workspace / "README.md").read_text(encoding="utf-8") == "old readme\n"
    assert (rig.workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"
    changed = {path for path in set(before_restore_hashes) | set(workspace_hashes(rig.workspace)) if before_restore_hashes.get(path) != workspace_hashes(rig.workspace).get(path)}
    assert changed == {"README.md"}


def test_restore_can_remove_applied_create_patch_without_deleting_directory(tmp_path: Path, monkeypatch) -> None:
    rig, result = apply_change(tmp_path, monkeypatch, path="src/generated_test.cpp", before=None, after="int generated = 1;\n")
    assert (rig.workspace / "src/generated_test.cpp").exists()

    restore = restore_apply(rig, result)

    assert restore.files_removed == 1
    assert not (rig.workspace / "src/generated_test.cpp").exists()
    assert (rig.workspace / "src").is_dir()
    assert (rig.workspace / "src/main.cpp").exists()
    assert (rig.workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"


def test_restore_can_restore_applied_delete_patch(tmp_path: Path, monkeypatch) -> None:
    rig, result = apply_change(tmp_path, monkeypatch, path="src/delete_me.cpp", before="int doomed = 1;\n", after=None)
    assert not (rig.workspace / "src/delete_me.cpp").exists()

    restore = restore_apply(rig, result)

    assert restore.files_restored == 1
    assert (rig.workspace / "src/delete_me.cpp").read_text(encoding="utf-8") == "int doomed = 1;\n"
    assert (rig.workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep me\n"


def test_apply_and_restore_audit_events_are_recorded(tmp_path: Path, monkeypatch) -> None:
    rig, result = apply_change(tmp_path, monkeypatch, path="README.md", before="old readme\n", after="new readme\n")
    restore_apply(rig, result)

    events = {entry["event"] for entry in audit_events(rig)}
    assert {
        "patch_apply_requested",
        "patch_apply_confirmed",
        "patch_apply_started",
        "patch_apply_preflight_passed",
        "patch_apply_rollback_snapshot_created",
        "patch_apply_staged",
        "patch_apply_completed",
        "rollback_restore_requested",
        "rollback_restore_confirmed",
        "rollback_restore_started",
        "rollback_restore_completed",
    }.issubset(events)
