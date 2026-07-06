from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService, hash_file
from backend.bridges.patch_export_service import BridgePatchExportService, sha256_file
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.patch_store import BridgePatchRecord
from backend.bridges.review_models import BridgeChangedFile, BridgeReviewSession
from backend.bridges.review_store import BridgeReviewStore


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_services(tmp_path: Path) -> tuple[BridgeDiffService, BridgePatchExportService, PatchPreflightService, BridgeAuditLog]:
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
    return reviews, exporter, preflight, audit


def add_review(
    reviews: BridgeDiffService,
    workspace: Path,
    *,
    path: str = "src/main.cpp",
    change_type: str = "modified",
    before: str | None = "old\n",
    after: str | None = "new\n",
    status: str = "approved",
    provider_id: str = "antigravity_cli_bridge",
    expires_at: datetime | None = None,
    safe: bool = True,
    preview_supported: bool = True,
) -> BridgeReviewSession:
    if before is not None:
        write(workspace / path, before)
    previous_hash = hash_file(workspace / path) if before is not None and (workspace / path).exists() else None
    new_hash = None
    if after is not None:
        temp = workspace / ".tmp-new"
        write(temp, after)
        new_hash = hash_file(temp)
        temp.unlink()
    now = datetime.now(timezone.utc)
    diff = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before or ''}+{after or ''}"
    review = BridgeReviewSession(
        review_id=f"review-{path.replace('/', '-').replace('.', '-')}-{change_type}",
        provider_id=provider_id,
        workspace_root=str(workspace),
        workspace_root_hash=hash_workspace_root(str(workspace.resolve())),
        status=status,  # type: ignore[arg-type]
        created_at=now,
        expires_at=expires_at or now + timedelta(hours=1),
        changed_files=(
            BridgeChangedFile(
                path=path,
                change_type=change_type,  # type: ignore[arg-type]
                safe=safe,
                previous_hash=previous_hash,
                new_hash=new_hash,
                diff_preview=diff if preview_supported else None,
                preview_supported=preview_supported,
                warning=None if preview_supported else "Binary or large file preview is not supported.",
            ),
        ),
        summary=f"1 {change_type}",
        decision="approved" if status == "approved" else None,
    )
    reviews._sessions[review.review_id] = review
    return review


def export_review(exporter: BridgePatchExportService, review: BridgeReviewSession) -> str:
    return exporter.export_patch(review.review_id).patch_id


def conflict_types(result: object) -> set[str]:
    return {item["type"] for item in result.to_dict()["conflicts"]}  # type: ignore[attr-defined]


def test_valid_approved_patch_returns_can_apply_true_but_apply_disabled(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is True
    assert result.apply_enabled is False
    assert result.integrity_status == "valid"
    assert result.review_status == "approved"
    assert result.workspace_status == "ok"
    assert result.files_to_modify == ("src/main.cpp",)
    assert result.conflicts == ()


def test_modified_patch_fails_integrity(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    patch_path = tmp_path / "patches" / patch_id
    patch_path.write_text(patch_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "patch_modified" in conflict_types(result)


def test_missing_patch_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    (tmp_path / "patches" / patch_id).unlink()

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "patch_missing" in conflict_types(result)


def test_missing_review_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    reviews._sessions.pop(review.review_id)

    result = preflight.preflight_patch(patch_id, workspace_root=workspace)

    assert result.can_apply is False
    assert "review_missing" in conflict_types(result)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", "review_not_approved"),
        ("rejected", "review_not_approved"),
    ],
)
def test_unapproved_review_fails(tmp_path: Path, status: str, expected: str) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, status=status)
    patch_id = export_review(exporter, review)

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert expected in conflict_types(result)


def test_expired_review_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    patch_id = export_review(exporter, review)

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "review_expired" in conflict_types(result)


def test_provider_mismatch_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    record = exporter.patch_store.get(patch_id)
    exporter.patch_store.upsert(replace(record, provider_id="codex_bridge"))

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "provider_mismatch" in conflict_types(result)


def test_unsafe_path_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id="review-unsafe",
        provider_id="antigravity_cli_bridge",
        workspace_root=str(workspace),
        workspace_root_hash=hash_workspace_root(str(workspace.resolve())),
        status="approved",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(BridgeChangedFile(path="../escape.cpp", change_type="created", safe=False),),
        summary="1 unsafe",
        decision="approved",
    )
    reviews._sessions[review.review_id] = review
    patch_path = tmp_path / "patches" / "unsafe.patch"
    write(patch_path, "diff --git a/../escape.cpp b/../escape.cpp\n")
    exporter.patch_store.upsert(
        BridgePatchRecord(
            patch_id="unsafe.patch",
            review_id=review.review_id,
            provider_id=review.provider_id,
            created_at=now,
            patch_path="unsafe.patch",
            metadata_path="unsafe.metadata.json",
            patch_size=patch_path.stat().st_size,
            patch_sha256=sha256_file(patch_path),
            integrity_status="valid",
            changed_file_count=1,
            created_files=("../escape.cpp",),
            modified_files=(),
            deleted_files=(),
            workspace_root_hash=review.workspace_root_hash or "",
            review_status_at_export="approved",
            apply_enabled=False,
        )
    )

    result = preflight.preflight_patch("unsafe.patch")

    assert result.can_apply is False
    assert "path_unsafe" in conflict_types(result)


def test_ignored_folder_path_fails(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, path="node_modules/pkg/index.js", change_type="created", before=None, after="x\n")
    patch_id = export_review(exporter, review)

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "ignored_path" in conflict_types(result)


def test_workspace_drift_returns_target_changed_conflict(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    write(workspace / "src/main.cpp", "changed locally\n")

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "target_changed" in conflict_types(result)


def test_target_missing_returns_conflict(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    (workspace / "src/main.cpp").unlink()

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "target_missing" in conflict_types(result)


def test_delete_conflict_returns_conflict(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace, change_type="deleted", before="old\n", after=None)
    patch_id = export_review(exporter, review)
    write(workspace / "src/main.cpp", "changed before delete\n")

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "delete_conflict" in conflict_types(result)


def test_parse_error_returns_conflict(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    patch_path = tmp_path / "patches" / patch_id
    patch_path.write_text("not a patch\n", encoding="utf-8")
    record = exporter.patch_store.get(patch_id)
    exporter.patch_store.upsert(replace(record, patch_sha256=sha256_file(patch_path)))

    result = preflight.preflight_patch(patch_id)

    assert result.can_apply is False
    assert "parse_error" in conflict_types(result)


def test_preflight_does_not_modify_active_workspace(tmp_path: Path) -> None:
    reviews, exporter, preflight, _ = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)
    before = (workspace / "src/main.cpp").read_text(encoding="utf-8")
    before_hash = hash_file(workspace / "src/main.cpp")

    preflight.preflight_patch(patch_id)

    assert (workspace / "src/main.cpp").read_text(encoding="utf-8") == before
    assert hash_file(workspace / "src/main.cpp") == before_hash


def test_audit_log_records_preflight_events(tmp_path: Path) -> None:
    reviews, exporter, preflight, audit = make_services(tmp_path)
    workspace = tmp_path / "workspace"
    review = add_review(reviews, workspace)
    patch_id = export_review(exporter, review)

    preflight.preflight_patch(patch_id)

    entries = audit.list_entries(100)
    assert any(entry["event"] == "patch_preflight_started" for entry in entries)
    assert any(entry["event"] == "patch_preflight_completed" for entry in entries)
    completed = [entry for entry in entries if entry["event"] == "patch_preflight_completed"][-1]
    assert completed["metadata"]["patch_id"] == patch_id
    assert "workspace_root_hash" in completed["metadata"]
