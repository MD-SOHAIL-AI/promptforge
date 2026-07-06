from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.patch_export_service import BridgePatchExportError, BridgePatchExportService
from backend.bridges.review_models import BridgeChangedFile, BridgeReviewSession
from backend.bridges.review_store import BridgeReviewStore


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, BridgeAuditLog]:
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
    return reviews, exporter, audit


def create_review(tmp_path: Path, before: dict[str, str], after: dict[str, str]) -> tuple[BridgeDiffService, BridgePatchExportService, BridgeReviewSession]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for path, content in before.items():
        write(workspace / path, content)
    reviews, exporter, _ = make_services(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    for path in before:
        if path not in after and (workspace / path).exists():
            (workspace / path).unlink()
    for path, content in after.items():
        write(workspace / path, content)
    review = reviews.create_review(provider_id="antigravity_cli_bridge", workspace_root=workspace, snapshot=snapshot)
    return reviews, exporter, review


def test_export_patch_from_review_with_created_file(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {}, {"src/main.cpp": "new\n"})

    export = exporter.export_patch(review.review_id)
    patch = exporter.patch_text(review.review_id)

    assert export.file_count == 1
    assert export.patch_sha256
    assert export.integrity_status == "valid"
    assert export.apply_enabled is False
    assert export.created_files == ("src/main.cpp",)
    assert "diff --git a/src/main.cpp b/src/main.cpp" in patch
    assert "+new" in patch


def test_export_patch_from_review_with_modified_file(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"src/main.cpp": "old\n"}, {"src/main.cpp": "new\n"})

    patch = exporter.patch_text(review.review_id)

    assert "--- a/src/main.cpp" in patch
    assert "+++ b/src/main.cpp" in patch
    assert "-old" in patch
    assert "+new" in patch


def test_export_patch_from_review_with_deleted_file(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "delete\n"}, {})

    patch = exporter.patch_text(review.review_id)

    assert "diff --git a/README.md b/README.md" in patch
    assert "-delete" in patch


def test_patch_uses_relative_paths_and_no_workspace_absolute_path(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})

    patch = exporter.patch_text(review.review_id)

    assert str(tmp_path) not in patch
    assert str(tmp_path / "workspace") not in patch
    assert "--- a/README.md" in patch
    metadata = exporter.patch_metadata(review.review_id).to_dict()
    assert str(tmp_path) not in str(metadata)
    assert metadata["modified_files"] == ["README.md"]


def test_binary_or_large_file_is_marked_unsupported(tmp_path: Path) -> None:
    reviews, exporter, _ = make_services(tmp_path)
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="review-binary",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(tmp_path / "workspace"),
        workspace_root_hash=hash_workspace_root(str(tmp_path / "workspace")),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(
            BridgeChangedFile(
                path="firmware.bin",
                change_type="modified",
                safe=True,
                preview_supported=False,
                warning="Binary or large file preview is not supported.",
            ),
        ),
        summary="1 modified",
    )
    reviews._sessions[review.review_id] = review

    patch = exporter.patch_text(review.review_id)

    assert "diff --git a/firmware.bin b/firmware.bin" in patch
    assert "Binary or unsupported diff preview" in patch


def test_export_fails_for_missing_review(tmp_path: Path) -> None:
    _, exporter, _ = make_services(tmp_path)

    with pytest.raises(BridgePatchExportError):
        exporter.export_patch("missing")


def test_export_fails_for_review_with_no_changed_files(tmp_path: Path) -> None:
    reviews, exporter, _ = make_services(tmp_path)
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="review-empty",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(tmp_path / "workspace"),
        workspace_root_hash=hash_workspace_root(str(tmp_path / "workspace")),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(),
        summary="0 created, 0 modified, 0 deleted",
    )
    reviews._sessions[review.review_id] = review

    with pytest.raises(BridgePatchExportError):
        exporter.export_patch(review.review_id)


def test_patch_content_is_capped(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "\n".join(str(i) for i in range(1000))})
    exporter.max_patch_chars = 200

    patch = exporter.patch_text(review.review_id)

    assert "[patch truncated]" in patch


def test_verify_patch_returns_valid_modified_and_missing(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    export = exporter.export_patch(review.review_id)

    assert exporter.verify_patch(review.review_id).integrity_status == "valid"

    patch_path = tmp_path / "patches" / export.patch_id
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    assert exporter.verify_patch(review.review_id).integrity_status == "modified"

    patch_path.unlink()
    assert exporter.verify_patch(review.review_id).integrity_status == "missing"


def test_open_patch_folder_rejects_unsafe_review_id(tmp_path: Path) -> None:
    reviews, exporter, _ = make_services(tmp_path)
    opened: list[Path] = []
    exporter.opener = opened.append
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="../escape",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(tmp_path / "workspace"),
        workspace_root_hash=hash_workspace_root(str(tmp_path / "workspace")),
        status="pending",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(
            BridgeChangedFile(
                path="README.md",
                change_type="modified",
                safe=True,
                diff_preview="--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
            ),
        ),
        summary="1 modified",
    )
    reviews._sessions[review.review_id] = review

    with pytest.raises(BridgePatchExportError):
        exporter.open_patch_folder(review.review_id)
    assert opened == []


def test_open_patch_folder_uses_patch_directory_only(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    opened: list[Path] = []
    exporter.opener = opened.append
    exporter.export_patch(review.review_id)

    result = exporter.open_patch_folder(review.review_id)

    assert result["opened"] is True
    assert opened
    assert opened[0].resolve() == (tmp_path / "patches").resolve()


def test_open_patch_folder_reports_opener_failure(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    exporter.export_patch(review.review_id)

    def fail_open(path: Path) -> None:
        raise OSError("open failed")

    exporter.opener = fail_open

    with pytest.raises(BridgePatchExportError, match="Patch folder could not be opened"):
        exporter.open_patch_folder(review.review_id)


def test_audit_log_records_patch_export(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})

    exporter.export_patch(review.review_id)

    entries = BridgeAuditLog(tmp_path / "audit.jsonl").list_entries(100)
    assert any(entry["event"] == "bridge_patch_exported" for entry in entries)
    assert any(entry["event"] == "bridge_patch_metadata_created" for entry in entries)


def test_audit_log_records_patch_verification(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    exporter.export_patch(review.review_id)
    exporter.verify_patch(review.review_id)

    entries = BridgeAuditLog(tmp_path / "audit.jsonl").list_entries(100)
    assert any(entry["event"] == "bridge_patch_verified" for entry in entries)


def test_exported_patch_appears_in_history(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    export = exporter.export_patch(review.review_id)

    history = exporter.list_patches()

    assert [patch.patch_id for patch in history] == [export.patch_id]
    assert history[0].modified_files == ("README.md",)
    assert history[0].apply_enabled is False


def test_patch_history_excludes_arbitrary_files(tmp_path: Path) -> None:
    _, exporter, _ = make_services(tmp_path)
    write(tmp_path / "patches" / "unindexed.patch", "diff --git a/x b/x\n")

    assert exporter.list_patches() == []


def test_delete_patch_removes_patch_and_metadata_but_not_review_or_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "old\n")
    reviews, exporter, _ = make_services(tmp_path)
    snapshot = reviews.snapshot_workspace(workspace)
    write(workspace / "README.md", "new\n")
    review = reviews.create_review(provider_id="antigravity_cli_bridge", workspace_root=workspace, snapshot=snapshot)
    export = exporter.export_patch(review.review_id)

    result = exporter.delete_patch(export.patch_id)

    assert result["deleted"] is True
    assert not (tmp_path / "patches" / export.patch_id).exists()
    assert not (tmp_path / "patches" / f"{review.review_id}.metadata.json").exists()
    assert reviews.get_review(review.review_id).review_id == review.review_id
    assert (workspace / "README.md").read_text(encoding="utf-8") == "new\n"


def test_cleanup_removes_old_patches_only(tmp_path: Path) -> None:
    reviews, exporter, _ = make_services(tmp_path)
    old_workspace = tmp_path / "old-workspace"
    recent_workspace = tmp_path / "recent-workspace"
    write(old_workspace / "README.md", "old\n")
    old_snapshot = reviews.snapshot_workspace(old_workspace)
    write(old_workspace / "README.md", "older\n")
    old_review = reviews.create_review(provider_id="antigravity_cli_bridge", workspace_root=old_workspace, snapshot=old_snapshot)
    old_export = exporter.export_patch(old_review.review_id)
    old_record = exporter.patch_store.get(old_export.patch_id)
    exporter.patch_store.upsert(replace(old_record, created_at=datetime.now(timezone.utc) - timedelta(days=45)))

    write(recent_workspace / "main.cpp", "old\n")
    recent_snapshot = reviews.snapshot_workspace(recent_workspace)
    write(recent_workspace / "main.cpp", "new\n")
    recent_review = reviews.create_review(provider_id="antigravity_cli_bridge", workspace_root=recent_workspace, snapshot=recent_snapshot)
    recent_export = exporter.export_patch(recent_review.review_id)

    cleanup = exporter.cleanup_patches(older_than_days=30, include_missing=True)

    assert cleanup["removed"] == 1
    assert not (tmp_path / "patches" / old_export.patch_id).exists()
    assert (tmp_path / "patches" / recent_export.patch_id).exists()
    assert [patch.patch_id for patch in exporter.list_patches()] == [recent_export.patch_id]


def test_cleanup_removes_missing_records_when_requested(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    export = exporter.export_patch(review.review_id)
    (tmp_path / "patches" / export.patch_id).unlink()

    cleanup = exporter.cleanup_patches(older_than_days=30, include_missing=True)

    assert cleanup["removed"] == 1
    assert exporter.list_patches() == []


def test_audit_log_records_patch_delete_and_cleanup(tmp_path: Path) -> None:
    _, exporter, review = create_review(tmp_path, {"README.md": "old\n"}, {"README.md": "new\n"})
    export = exporter.export_patch(review.review_id)
    exporter.delete_patch(export.patch_id)
    exporter.cleanup_patches(older_than_days=30, include_missing=True)

    entries = BridgeAuditLog(tmp_path / "audit.jsonl").list_entries(100)
    assert any(entry["event"] == "bridge_patch_deleted" for entry in entries)
    assert any(entry["event"] == "bridge_patch_cleanup_started" for entry in entries)
    assert any(entry["event"] == "bridge_patch_cleanup_completed" for entry in entries)
