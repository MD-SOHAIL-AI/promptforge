from __future__ import annotations

from pathlib import Path

import pytest

import backend.bridges.patch_apply_service as apply_module
from backend.bridges.audit_log import BridgeAuditLog
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.patch_apply_service import (
    PATCH_APPLY_DISABLED_MESSAGE,
    PATCH_APPLY_REQUIRES_RESTORE_MESSAGE,
    PatchApplyError,
    PatchApplyService,
)
from backend.bridges.patch_export_service import BridgePatchExportService
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_restore_apply_service import RollbackRestoreApplyService
from backend.bridges.rollback_restore_service import RollbackRestorePreflightService
from backend.bridges.rollback_service import RollbackSnapshotService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, PatchApplyService, BridgeAuditLog]:
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
    apply = PatchApplyService(
        apply_directory=tmp_path / "patch-applies",
        patch_store=exporter.patch_store,
        review_service=reviews,
        preflight_service=preflight,
        rollback_service=rollback,
        restore_apply_service=restore,
        audit_log=audit,
    )
    return reviews, exporter, apply, audit


def exported_patch(tmp_path: Path, *, changes: dict[str, tuple[str | None, str | None]]) -> tuple[Path, str, PatchApplyService, BridgeAuditLog]:
    reviews, exporter, apply, audit = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    for path, (before, _) in changes.items():
        if before is not None:
            write(workspace / path, before)
    workspace.mkdir(parents=True, exist_ok=True)
    snapshot = reviews.snapshot_workspace(workspace)
    for path, (_, after) in changes.items():
        target = workspace / path
        if after is None:
            target.unlink()
        else:
            write(target, after)
    review = reviews.create_review(provider_id="antigravity_cli_bridge", workspace_root=workspace, snapshot=snapshot)
    reviews.approve_review(review.review_id)
    patch_id = exporter.export_patch(review.review_id).patch_id
    for path, (before, _) in changes.items():
        target = workspace / path
        if before is None:
            if target.exists():
                target.unlink()
        else:
            write(target, before)
    return workspace, patch_id, apply, audit


def enable_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")


def test_apply_disabled_when_patch_flag_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace, patch_id, apply, _ = exported_patch(tmp_path, changes={"README.md": ("old\n", "new\n")})

    with pytest.raises(PatchApplyError, match=PATCH_APPLY_DISABLED_MESSAGE):
        apply.apply_patch(patch_id, workspace_root=workspace, confirmation="APPLY")


def test_apply_disabled_when_restore_flag_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    workspace, patch_id, apply, _ = exported_patch(tmp_path, changes={"README.md": ("old\n", "new\n")})

    with pytest.raises(PatchApplyError, match=PATCH_APPLY_REQUIRES_RESTORE_MESSAGE):
        apply.apply_patch(patch_id, workspace_root=workspace, confirmation="APPLY")


def test_apply_requires_exact_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_apply(monkeypatch)
    workspace, patch_id, apply, _ = exported_patch(tmp_path, changes={"README.md": ("old\n", "new\n")})

    with pytest.raises(PatchApplyError, match="confirmation APPLY"):
        apply.apply_patch(patch_id, workspace_root=workspace, confirmation="apply")


def test_apply_modifies_creates_deletes_and_persists_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_apply(monkeypatch)
    workspace, patch_id, apply, audit = exported_patch(
        tmp_path,
        changes={
            "README.md": ("old\n", "new\n"),
            "src/new.cpp": (None, "created\n"),
            "src/remove.cpp": ("remove\n", None),
        },
    )
    write(workspace / "unrelated.txt", "keep\n")

    result = apply.apply_patch(patch_id, workspace_root=workspace, confirmation="APPLY")

    assert result.status == "applied"
    assert result.files_created == 1
    assert result.files_modified == 1
    assert result.files_deleted == 1
    assert result.rollback_id
    assert result.rollback_available is True
    assert (workspace / "README.md").read_text(encoding="utf-8") == "new\n"
    assert (workspace / "src/new.cpp").read_text(encoding="utf-8") == "created\n"
    assert not (workspace / "src/remove.cpp").exists()
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"
    assert (tmp_path / "patch-applies" / result.apply_id / "metadata.json").exists()
    assert apply.get_apply(result.apply_id).rollback_id == result.rollback_id
    assert apply.list_applies()[0].apply_id == result.apply_id
    events = [entry["event"] for entry in audit.list_entries(500)]
    assert "patch_apply_requested" in events
    assert "patch_apply_confirmed" in events
    assert "patch_apply_preflight_passed" in events
    assert "patch_apply_rollback_snapshot_created" in events
    assert "patch_apply_staged" in events
    assert "patch_apply_completed" in events


def test_modified_patch_is_rejected_before_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_apply(monkeypatch)
    workspace, patch_id, apply, _ = exported_patch(tmp_path, changes={"README.md": ("old\n", "new\n")})
    patch_path = tmp_path / "patches" / patch_id
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")

    with pytest.raises(PatchApplyError, match="SHA-256"):
        apply.apply_patch(patch_id, workspace_root=workspace, confirmation="APPLY")
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_failed_write_triggers_auto_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_apply(monkeypatch)
    workspace, patch_id, apply, audit = exported_patch(
        tmp_path,
        changes={
            "one.txt": ("old one\n", "new one\n"),
            "two.txt": ("old two\n", "new two\n"),
        },
    )
    original_replace = apply_module.replace_file_atomic
    calls = {"count": 0}

    def flaky_replace(target: Path, content: bytes) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            original_replace(target, content)
            return
        raise OSError("simulated write failure")

    monkeypatch.setattr(apply_module, "replace_file_atomic", flaky_replace)

    result = apply.apply_patch(patch_id, workspace_root=workspace, confirmation="APPLY")

    assert result.status == "failed_rolled_back"
    assert result.files_failed == 1
    assert (workspace / "one.txt").read_text(encoding="utf-8") == "old one\n"
    assert (workspace / "two.txt").read_text(encoding="utf-8") == "old two\n"
    events = [entry["event"] for entry in audit.list_entries(500)]
    assert "patch_apply_auto_rollback_started" in events
    assert "patch_apply_auto_rollback_completed" in events
