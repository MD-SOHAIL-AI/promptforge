"""Feature-flagged safe patch apply service."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import hash_file
from .patch_apply_engine import PatchApplyEngine, PatchApplyEngineError, PatchApplyStagedFile, replace_file_atomic
from .patch_apply_models import PatchApplyFileResult, PatchApplyResult, patch_apply_result_from_dict
from .patch_export_service import sha256_file
from .patch_preflight_service import PatchPreflightService
from .patch_store import BridgePatchRecord, BridgePatchStore, BridgePatchStoreError
from .review_models import BridgeAuditEntry, BridgeReviewSession
from .rollback_restore_apply_service import ROLLBACK_RESTORE_FLAG, RollbackRestoreApplyService
from .rollback_service import RollbackSnapshotService


PATCH_APPLY_FLAG = "FORGEX_ENABLE_PATCH_APPLY"
PATCH_APPLY_DISABLED_MESSAGE = (
    "Patch apply is disabled. Enable FORGEX_ENABLE_PATCH_APPLY=1 for development testing."
)
PATCH_APPLY_REQUIRES_RESTORE_MESSAGE = (
    "Patch apply requires rollback restore support. Enable FORGEX_ENABLE_ROLLBACK_RESTORE=1."
)


class PatchApplyError(ValueError):
    code = "PATCH_APPLY_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class PatchApplyService:
    def __init__(
        self,
        *,
        apply_directory: str | Path,
        patch_store: BridgePatchStore,
        review_service: object,
        preflight_service: PatchPreflightService,
        rollback_service: RollbackSnapshotService,
        restore_apply_service: RollbackRestoreApplyService,
        audit_log: BridgeAuditLog | None = None,
        engine: PatchApplyEngine | None = None,
    ) -> None:
        self.apply_directory = Path(apply_directory)
        self.patch_store = patch_store
        self.review_service = review_service
        self.preflight_service = preflight_service
        self.rollback_service = rollback_service
        self.restore_apply_service = restore_apply_service
        self.audit_log = audit_log
        self.engine = engine or PatchApplyEngine()

    def patch_apply_feature_flag_enabled(self) -> bool:
        return os.getenv(PATCH_APPLY_FLAG, "").strip() == "1"

    def rollback_restore_feature_flag_enabled(self) -> bool:
        return os.getenv(ROLLBACK_RESTORE_FLAG, "").strip() == "1"

    def is_enabled(self) -> bool:
        return self.patch_apply_feature_flag_enabled() and self.rollback_restore_feature_flag_enabled()

    def apply_patch(self, patch_id: str, *, workspace_root: str | Path, confirmation: str) -> PatchApplyResult:
        record = self._load_record(patch_id)
        apply_id = f"apply-{uuid.uuid4().hex}"
        root = Path(workspace_root).expanduser().resolve()
        root_hash = hash_workspace_root(str(root))
        started_at = datetime.now(timezone.utc)
        self._record_audit("patch_apply_requested", record, apply_id=apply_id, rollback_id=None, workspace_root_hash=root_hash)

        try:
            if not self.patch_apply_feature_flag_enabled():
                raise self._blocked(record, apply_id, root_hash, PATCH_APPLY_DISABLED_MESSAGE, {"feature_flag": PATCH_APPLY_FLAG})
            if not self.rollback_restore_feature_flag_enabled():
                raise self._blocked(record, apply_id, root_hash, PATCH_APPLY_REQUIRES_RESTORE_MESSAGE, {"feature_flag": ROLLBACK_RESTORE_FLAG})
            if confirmation != "APPLY":
                raise self._blocked(record, apply_id, root_hash, "Patch apply requires exact confirmation APPLY.", {"patch_id": patch_id})
            self._record_audit("patch_apply_confirmed", record, apply_id=apply_id, rollback_id=None, workspace_root_hash=root_hash)

            patch_path = self._verified_patch_path(record)
            review = self._load_review(record)
            self._check_review(record, review)
            preflight = self.preflight_service.preflight_patch(patch_id, workspace_root=root)
            if not preflight.can_apply:
                raise self._blocked(
                    record,
                    apply_id,
                    root_hash,
                    "Patch apply requires a fresh passing preflight.",
                    {"patch_id": patch_id, "conflict_count": len(preflight.conflicts)},
                )
            self._record_audit("patch_apply_preflight_passed", record, apply_id=apply_id, rollback_id=None, workspace_root_hash=root_hash)

            snapshot = self.rollback_service.create_snapshot(patch_id, workspace_root=root)
            self._record_audit("patch_apply_rollback_snapshot_created", record, apply_id=apply_id, rollback_id=snapshot.rollback_id, workspace_root_hash=root_hash)

            patch_text = patch_path.read_text(encoding="utf-8", errors="strict")
            staged = self.engine.stage(
                patch_text,
                workspace_root=root,
                files_to_create=preflight.files_to_create,
                files_to_modify=preflight.files_to_modify,
                files_to_delete=preflight.files_to_delete,
            )
            self._record_audit("patch_apply_staged", record, apply_id=apply_id, rollback_id=snapshot.rollback_id, workspace_root_hash=root_hash)
            self._record_audit("patch_apply_started", record, apply_id=apply_id, rollback_id=snapshot.rollback_id, workspace_root_hash=root_hash)
            result = self._write_staged(record, apply_id, snapshot.rollback_id, root, root_hash, started_at, staged)
            self._mark_snapshot_applied(snapshot.rollback_id, apply_id, staged if result.status == "applied" else ())
            self._persist_result(result)
            event = "patch_apply_completed" if result.status == "applied" else "patch_apply_failed"
            self._record_audit(event, record, apply_id=apply_id, rollback_id=snapshot.rollback_id, workspace_root_hash=root_hash, result=result)
            return result
        except PatchApplyError:
            raise
        except (PatchApplyEngineError, UnicodeDecodeError) as exc:
            self._record_audit("patch_apply_blocked", record, apply_id=apply_id, rollback_id=None, workspace_root_hash=root_hash)
            details = getattr(exc, "details", {})
            raise PatchApplyError(str(exc), dict(details) if isinstance(details, dict) else {}) from exc

    def list_applies(self) -> list[PatchApplyResult]:
        results: list[PatchApplyResult] = []
        if not self.apply_directory.exists():
            return []
        for metadata_path in self.apply_directory.glob("*/metadata.json"):
            try:
                results.append(patch_apply_result_from_dict(json.loads(metadata_path.read_text(encoding="utf-8"))))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(results, key=lambda item: item.completed_at, reverse=True)

    def get_apply(self, apply_id: str) -> PatchApplyResult:
        metadata_path = self._apply_dir(apply_id) / "metadata.json"
        if not metadata_path.exists():
            raise PatchApplyError("Patch apply result was not found.", {"apply_id": apply_id})
        return patch_apply_result_from_dict(json.loads(metadata_path.read_text(encoding="utf-8")))

    def _write_staged(
        self,
        record: BridgePatchRecord,
        apply_id: str,
        rollback_id: str,
        root: Path,
        root_hash: str,
        started_at: datetime,
        staged: tuple[PatchApplyStagedFile, ...],
    ) -> PatchApplyResult:
        results: list[PatchApplyFileResult] = []
        mutated = False
        try:
            for item in staged:
                self._verify_target_before_write(root, item)
                if item.operation in {"create", "modify"}:
                    if item.content is None or item.after_hash is None:
                        raise PatchApplyError("Staged file content is missing.", {"path": item.path})
                    replace_file_atomic(item.target, item.content)
                    mutated = True
                    actual = hash_file(item.target)
                    if actual != item.after_hash:
                        raise PatchApplyError("Written file hash did not match staged output.", {"path": item.path})
                    results.append(PatchApplyFileResult(item.path, item.operation, "success", item.before_hash, actual, "File written."))
                elif item.operation == "delete":
                    if not item.target.exists() or not item.target.is_file() or item.target.is_symlink():
                        raise PatchApplyError("Delete target is not a regular file.", {"path": item.path})
                    item.target.unlink()
                    mutated = True
                    if item.target.exists():
                        raise PatchApplyError("Delete target still exists after apply.", {"path": item.path})
                    results.append(PatchApplyFileResult(item.path, "delete", "success", item.before_hash, None, "File deleted."))
                else:
                    raise PatchApplyError("Unsupported staged operation.", {"path": item.path})
            completed_at = datetime.now(timezone.utc)
            return PatchApplyResult(
                apply_id=apply_id,
                patch_id=record.patch_id,
                review_id=record.review_id,
                provider_id=record.provider_id,
                rollback_id=rollback_id,
                workspace_root_hash=root_hash,
                status="applied",
                started_at=started_at,
                completed_at=completed_at,
                files_created=sum(1 for item in results if item.operation == "create" and item.status == "success"),
                files_modified=sum(1 for item in results if item.operation == "modify" and item.status == "success"),
                files_deleted=sum(1 for item in results if item.operation == "delete" and item.status == "success"),
                files_failed=0,
                rollback_available=True,
                apply_enabled=True,
                restore_enabled=True,
                results=tuple(results),
            )
        except Exception as exc:
            failed = self._with_failure_result(results, staged, exc)
            if not mutated:
                completed_at = datetime.now(timezone.utc)
                return self._failure_result(record, apply_id, rollback_id, root_hash, started_at, completed_at, failed, "failed")
            rolled_back = self._auto_rollback(record, apply_id, rollback_id, root, root_hash)
            status = "failed_rolled_back" if rolled_back else "failed_rollback_failed"
            completed_at = datetime.now(timezone.utc)
            return self._failure_result(record, apply_id, rollback_id, root_hash, started_at, completed_at, failed, status)

    def _verify_target_before_write(self, root: Path, item: PatchApplyStagedFile) -> None:
        target = item.target.resolve()
        target.relative_to(root)
        if item.operation == "create" and target.exists():
            raise PatchApplyError("Create target appeared after staging.", {"path": item.path})
        if item.operation in {"modify", "delete"}:
            if not target.exists() or not target.is_file() or target.is_symlink():
                raise PatchApplyError("Patch target changed after staging.", {"path": item.path})
            current_hash = hash_file(target)
            if item.before_hash and current_hash != item.before_hash:
                raise PatchApplyError("Patch target changed after staging.", {"path": item.path})

    def _auto_rollback(
        self,
        record: BridgePatchRecord,
        apply_id: str,
        rollback_id: str,
        root: Path,
        root_hash: str,
    ) -> bool:
        self._record_audit("patch_apply_auto_rollback_started", record, apply_id=apply_id, rollback_id=rollback_id, workspace_root_hash=root_hash)
        try:
            snapshot = self.restore_apply_service.rollback_service.get_snapshot(rollback_id)
            restored = 0
            removed = 0
            failed = 0
            for entry in snapshot.files:
                result = self.restore_apply_service._restore_entry(snapshot, root, entry)
                if result.status == "failed":
                    failed += 1
                elif result.operation == "restore_file":
                    restored += 1
                elif result.operation == "remove_created_file":
                    removed += 1
            if failed:
                self._record_audit(
                    "patch_apply_auto_rollback_failed",
                    record,
                    apply_id=apply_id,
                    rollback_id=rollback_id,
                    workspace_root_hash=root_hash,
                    files_failed=failed,
                )
                return False
            self._record_audit(
                "patch_apply_auto_rollback_completed",
                record,
                apply_id=apply_id,
                rollback_id=rollback_id,
                workspace_root_hash=root_hash,
                files_created=0,
                files_modified=restored,
                files_deleted=removed,
            )
            return True
        except Exception:
            self._record_audit("patch_apply_auto_rollback_failed", record, apply_id=apply_id, rollback_id=rollback_id, workspace_root_hash=root_hash)
            return False

    def _failure_result(
        self,
        record: BridgePatchRecord,
        apply_id: str,
        rollback_id: str,
        root_hash: str,
        started_at: datetime,
        completed_at: datetime,
        results: tuple[PatchApplyFileResult, ...],
        status: str,
    ) -> PatchApplyResult:
        return PatchApplyResult(
            apply_id=apply_id,
            patch_id=record.patch_id,
            review_id=record.review_id,
            provider_id=record.provider_id,
            rollback_id=rollback_id,
            workspace_root_hash=root_hash,
            status=status,  # type: ignore[arg-type]
            started_at=started_at,
            completed_at=completed_at,
            files_created=sum(1 for item in results if item.operation == "create" and item.status == "success"),
            files_modified=sum(1 for item in results if item.operation == "modify" and item.status == "success"),
            files_deleted=sum(1 for item in results if item.operation == "delete" and item.status == "success"),
            files_failed=sum(1 for item in results if item.status == "failed"),
            rollback_available=True,
            apply_enabled=True,
            restore_enabled=True,
            results=results,
        )

    def _with_failure_result(
        self,
        results: list[PatchApplyFileResult],
        staged: tuple[PatchApplyStagedFile, ...],
        exc: Exception,
    ) -> tuple[PatchApplyFileResult, ...]:
        completed_paths = {item.path for item in results}
        remaining = list(results)
        for item in staged:
            if item.path in completed_paths:
                continue
            remaining.append(
                PatchApplyFileResult(
                    path=item.path,
                    operation=item.operation,  # type: ignore[arg-type]
                    status="failed",
                    before_hash=item.before_hash,
                    after_hash=item.after_hash,
                    message=f"Apply failed: {type(exc).__name__}.",
                )
            )
            break
        return tuple(remaining)

    def _verified_patch_path(self, record: BridgePatchRecord) -> Path:
        try:
            patch_path = self.patch_store.safe_child(record.patch_path)
        except BridgePatchStoreError as exc:
            raise PatchApplyError("Patch record path is not managed by ForgeX.", {"patch_id": record.patch_id}) from exc
        if not patch_path.exists():
            raise PatchApplyError("Exported patch file is missing.", {"patch_id": record.patch_id})
        current_hash = sha256_file(patch_path)
        if not record.patch_sha256 or current_hash != record.patch_sha256:
            raise PatchApplyError("Exported patch content no longer matches recorded SHA-256.", {"patch_id": record.patch_id})
        return patch_path

    def _load_record(self, patch_id: str) -> BridgePatchRecord:
        try:
            return self.patch_store.get(patch_id)
        except BridgePatchStoreError as exc:
            raise PatchApplyError("Patch record was not found.", {"patch_id": patch_id}) from exc

    def _load_review(self, record: BridgePatchRecord) -> BridgeReviewSession:
        getter = getattr(self.review_service, "get_review", None)
        if callable(getter):
            try:
                return getter(record.review_id)
            except Exception as exc:
                raise PatchApplyError("Bridge review was not found.", {"review_id": record.review_id}) from exc
        sessions = getattr(self.review_service, "_sessions", {})
        review = sessions.get(record.review_id) if isinstance(sessions, dict) else None
        if review is None:
            raise PatchApplyError("Bridge review was not found.", {"review_id": record.review_id})
        return review

    def _check_review(self, record: BridgePatchRecord, review: BridgeReviewSession) -> None:
        now = datetime.now(timezone.utc)
        if now > review.expires_at:
            raise PatchApplyError("Bridge review is expired.", {"review_id": review.review_id})
        if review.status != "approved":
            raise PatchApplyError("Bridge review must be approved before applying a patch.", {"review_id": review.review_id})
        if review.provider_id != record.provider_id:
            raise PatchApplyError("Patch provider does not match review provider.", {"review_id": review.review_id})

    def _mark_snapshot_applied(self, rollback_id: str, apply_id: str, staged: tuple[PatchApplyStagedFile, ...] = ()) -> None:
        try:
            snapshot = self.rollback_service.get_snapshot(rollback_id)
            post_hashes = {item.path: item.after_hash for item in staged}
            files = tuple(
                replace(item, post_apply_hash=post_hashes.get(item.path))
                for item in snapshot.files
            )
            metadata_path = self.rollback_service._snapshot_dir(rollback_id) / "metadata.json"
            metadata_path.write_text(
                json.dumps(replace(snapshot, apply_id=apply_id, restore_enabled=True, files=files).to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception:
            return

    def _persist_result(self, result: PatchApplyResult) -> None:
        apply_dir = self._apply_dir(result.apply_id)
        apply_dir.mkdir(parents=True, exist_ok=False)
        metadata = result.to_dict()
        metadata["created_at"] = metadata["started_at"]
        metadata["file_results"] = metadata["results"]
        (apply_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.apply_directory.mkdir(parents=True, exist_ok=True)
        with (self.apply_directory / "index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")

    def _apply_dir(self, apply_id: str) -> Path:
        if "/" in apply_id or "\\" in apply_id or apply_id in {"", ".", ".."}:
            raise PatchApplyError("Patch apply ID is invalid.", {"apply_id": apply_id})
        path = (self.apply_directory / apply_id).resolve()
        try:
            path.relative_to(self.apply_directory.resolve())
        except ValueError as exc:
            raise PatchApplyError("Patch apply path escaped state directory.", {"apply_id": apply_id}) from exc
        return path

    def _blocked(
        self,
        record: BridgePatchRecord,
        apply_id: str,
        workspace_root_hash: str,
        message: str,
        details: dict[str, object],
    ) -> PatchApplyError:
        self._record_audit("patch_apply_blocked", record, apply_id=apply_id, rollback_id=None, workspace_root_hash=workspace_root_hash)
        return PatchApplyError(message, details)

    def _record_audit(
        self,
        event: str,
        record: BridgePatchRecord,
        *,
        apply_id: str,
        rollback_id: str | None,
        workspace_root_hash: str,
        result: PatchApplyResult | None = None,
        files_created: int = 0,
        files_modified: int = 0,
        files_deleted: int = 0,
        files_failed: int = 0,
    ) -> None:
        if self.audit_log is None:
            return
        if result is not None:
            files_created = result.files_created
            files_modified = result.files_modified
            files_deleted = result.files_deleted
            files_failed = result.files_failed
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=record.provider_id,
                workspace_root_hash=workspace_root_hash,
                changed_file_count=record.changed_file_count,
                approved=False,
                review_id=record.review_id,
                metadata={
                    "apply_id": apply_id,
                    "patch_id": record.patch_id,
                    "review_id": record.review_id,
                    "rollback_id": rollback_id,
                    "provider_id": record.provider_id,
                    "files_created": files_created,
                    "files_modified": files_modified,
                    "files_deleted": files_deleted,
                    "files_failed": files_failed,
                    "workspace_root_hash": workspace_root_hash,
                },
            )
        )
