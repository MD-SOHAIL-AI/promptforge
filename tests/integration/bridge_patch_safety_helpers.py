from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService, hash_file
from backend.bridges.patch_export_service import BridgePatchExportService, sha256_file
from backend.bridges.patch_apply_service import PatchApplyService
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.patch_store import BridgePatchRecord
from backend.bridges.review_models import BridgeAuditEntry, BridgeChangedFile, BridgeReviewSession
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_restore_service import RollbackRestorePreflightService
from backend.bridges.rollback_restore_apply_service import RollbackRestoreApplyService
from backend.bridges.rollback_service import RollbackSnapshotService


PROVIDER_ID = "antigravity_cli_bridge"


class BridgePatchSafetyRig:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.workspace = tmp_path / "workspace"
        self.patch_dir = tmp_path / "patches"
        self.rollback_dir = tmp_path / "patch-rollback"
        self.audit = BridgeAuditLog(tmp_path / "audit.jsonl")
        self.reviews = BridgeDiffService(
            audit_log=self.audit,
            store=BridgeReviewStore(
                snapshots_path=tmp_path / "snapshots.jsonl",
                reviews_path=tmp_path / "reviews.jsonl",
            ),
        )
        self.exporter = BridgePatchExportService(
            review_service=self.reviews,
            patch_directory=self.patch_dir,
            audit_log=self.audit,
        )
        self.preflight = PatchPreflightService(
            patch_store=self.exporter.patch_store,
            review_service=self.reviews,
            audit_log=self.audit,
        )
        self.rollback = RollbackSnapshotService(
            rollback_directory=self.rollback_dir,
            preflight_service=self.preflight,
            audit_log=self.audit,
        )
        self.restore_preflight = RollbackRestorePreflightService(
            rollback_service=self.rollback,
            audit_log=self.audit,
        )
        self.restore_apply = RollbackRestoreApplyService(
            restore_directory=tmp_path / "rollback-restores",
            rollback_service=self.rollback,
            restore_preflight_service=self.restore_preflight,
            audit_log=self.audit,
        )
        self.apply = PatchApplyService(
            apply_directory=tmp_path / "patch-applies",
            patch_store=self.exporter.patch_store,
            review_service=self.reviews,
            preflight_service=self.preflight,
            rollback_service=self.rollback,
            restore_apply_service=self.restore_apply,
            audit_log=self.audit,
        )


def make_rig(tmp_path: Path) -> BridgePatchSafetyRig:
    rig = BridgePatchSafetyRig(tmp_path)
    create_workspace(rig.workspace)
    return rig


def create_workspace(workspace: Path) -> None:
    write(workspace / "README.md", "old readme\n")
    write(workspace / "platformio.ini", "[env:esp32dev]\nplatform = espressif32\n")
    write(workspace / "src" / "main.cpp", "void setup() {}\nvoid loop() {}\n")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def workspace_hashes(workspace: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for current_root, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [name for name in dirnames if name.casefold() not in {".git", ".pio", "node_modules", "dist", "build", ".next", ".forgex"}]
        current = Path(current_root)
        for filename in filenames:
            path = current / filename
            relative = path.resolve().relative_to(workspace.resolve()).as_posix()
            hashes[relative] = hash_file(path)
    return hashes


def assert_workspace_unchanged(workspace: Path, before: dict[str, str]) -> None:
    assert workspace_hashes(workspace) == before


def add_review(
    rig: BridgePatchSafetyRig,
    *,
    path: str = "README.md",
    change_type: str = "modified",
    before: str | None = "old readme\n",
    after: str | None = "new readme\n",
    status: str = "pending",
    provider_id: str = PROVIDER_ID,
    expires_at: datetime | None = None,
    safe: bool = True,
) -> BridgeReviewSession:
    if before is not None:
        write(rig.workspace / path, before)
    previous_hash = hash_file(rig.workspace / path) if before is not None and (rig.workspace / path).exists() else None
    new_hash = hash_text(after) if after is not None else None
    now = datetime.now(timezone.utc)
    review = BridgeReviewSession(
        review_id=f"review-{len(rig.reviews._sessions) + 1}",
        provider_id=provider_id,
        workspace_root=str(rig.workspace),
        workspace_root_hash=hash_workspace_root(str(rig.workspace.resolve())),
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
                diff_preview=diff_for(path, before, after),
                preview_supported=True,
            ),
        ),
        summary=f"1 {change_type}",
        decision="approved" if status == "approved" else None,
    )
    rig.reviews._sessions[review.review_id] = review
    rig.audit.record(
        BridgeAuditEntry(
            event="review_created",
            provider_id=provider_id,
            workspace_root_hash=review.workspace_root_hash or "",
            changed_file_count=len(review.changed_files),
            approved=False,
            review_id=review.review_id,
        )
    )
    return review


def approve_review(rig: BridgePatchSafetyRig, review: BridgeReviewSession) -> BridgeReviewSession:
    approved, _ = rig.reviews.approve_review(review.review_id)
    return approved


def add_approved_review(rig: BridgePatchSafetyRig, **kwargs: object) -> BridgeReviewSession:
    return approve_review(rig, add_review(rig, **kwargs))


def export_and_verify(rig: BridgePatchSafetyRig, review: BridgeReviewSession) -> str:
    export = rig.exporter.export_patch(review.review_id)
    verified = rig.exporter.verify_patch(review.review_id)
    assert verified.integrity_status == "valid"
    assert verified.apply_enabled is False
    return export.patch_id


def make_manual_patch_record(
    rig: BridgePatchSafetyRig,
    *,
    patch_id: str,
    path: str,
    change_type: str = "created",
    safe: bool = True,
) -> str:
    now = datetime.now(timezone.utc)
    normalized_path = path.replace("\\", "/")
    target = rig.workspace / normalized_path
    previous_hash = hash_file(target) if change_type in {"modified", "deleted"} and target.exists() else None
    review = BridgeReviewSession(
        review_id=f"review-{patch_id}",
        provider_id=PROVIDER_ID,
        workspace_root=str(rig.workspace),
        workspace_root_hash=hash_workspace_root(str(rig.workspace.resolve())),
        status="approved",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        changed_files=(
            BridgeChangedFile(
                path=path,
                change_type=change_type,  # type: ignore[arg-type]
                safe=safe,
                previous_hash=previous_hash,
                new_hash=hash_text("new\n"),
                diff_preview=f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+new\n",
            ),
        ),
        summary="manual safety patch",
        decision="approved",
    )
    rig.reviews._sessions[review.review_id] = review
    patch_path = rig.patch_dir / patch_id
    write(patch_path, f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+new\n")
    record = BridgePatchRecord(
        patch_id=patch_id,
        review_id=review.review_id,
        provider_id=PROVIDER_ID,
        created_at=now,
        patch_path=patch_id,
        metadata_path=f"{patch_id}.metadata.json",
        patch_size=patch_path.stat().st_size,
        patch_sha256=sha256_file(patch_path),
        integrity_status="valid",
        changed_file_count=1,
        created_files=(path,) if change_type == "created" else (),
        modified_files=(path,) if change_type == "modified" else (),
        deleted_files=(path,) if change_type == "deleted" else (),
        workspace_root_hash=review.workspace_root_hash or "",
        review_status_at_export="approved",
        apply_enabled=False,
    )
    rig.exporter.patch_store.upsert(record)
    return patch_id


def make_patch_old(rig: BridgePatchSafetyRig, patch_id: str, *, days: int = 45) -> None:
    record = rig.exporter.patch_store.get(patch_id)
    rig.exporter.patch_store.upsert(replace(record, created_at=datetime.now(timezone.utc) - timedelta(days=days)))


def make_snapshot_old(rig: BridgePatchSafetyRig, rollback_id: str, *, days: int = 45) -> None:
    metadata_path = rig.rollback_dir / rollback_id / "metadata.json"
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    payload["created_at"] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")
    metadata_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def audit_events(rig: BridgePatchSafetyRig) -> list[dict[str, object]]:
    return rig.audit.list_entries(500)


def conflict_types(result: object) -> set[str]:
    return {item["type"] for item in result.to_dict()["conflicts"]}  # type: ignore[attr-defined]


def hash_text(content: str | None) -> str | None:
    if content is None:
        return None
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def diff_for(path: str, before: str | None, after: str | None) -> str:
    before_lines = (before or "").splitlines()
    after_lines = (after or "").splitlines()
    if before is None:
        return f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{after_lines[0] if after_lines else ''}\n"
    if after is None:
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ /dev/null\n@@ -1 +0,0 @@\n-{before_lines[0] if before_lines else ''}\n"
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{before_lines[0] if before_lines else ''}\n+{after_lines[0] if after_lines else ''}\n"
