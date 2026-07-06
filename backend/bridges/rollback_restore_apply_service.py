"""Feature-flagged rollback restore execution."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import contains_ignored_path_part, hash_file, is_safe_relative_path
from .review_models import BridgeAuditEntry
from .rollback_models import RollbackFileBackup, RollbackSnapshot
from .rollback_restore_apply_models import (
    RollbackRestoreFileResult,
    RollbackRestoreResult,
    rollback_restore_result_from_dict,
)
from .rollback_restore_service import RollbackRestorePreflightService
from .rollback_service import RollbackSnapshotError, RollbackSnapshotService


ROLLBACK_RESTORE_FLAG = "FORGEX_ENABLE_ROLLBACK_RESTORE"
ROLLBACK_RESTORE_DISABLED_MESSAGE = (
    "Rollback restore is disabled. Enable FORGEX_ENABLE_ROLLBACK_RESTORE=1 for development testing."
)


class RollbackRestoreApplyError(ValueError):
    code = "ROLLBACK_RESTORE_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class RollbackRestoreApplyService:
    def __init__(
        self,
        *,
        restore_directory: str | Path,
        rollback_service: RollbackSnapshotService,
        restore_preflight_service: RollbackRestorePreflightService,
        audit_log: BridgeAuditLog | None = None,
    ) -> None:
        self.restore_directory = Path(restore_directory)
        self.rollback_service = rollback_service
        self.restore_preflight_service = restore_preflight_service
        self.audit_log = audit_log

    def is_enabled(self) -> bool:
        return os.getenv(ROLLBACK_RESTORE_FLAG, "").strip() == "1"

    def restore_snapshot(self, rollback_id: str, *, workspace_root: str | Path, confirmation: str) -> RollbackRestoreResult:
        snapshot = self._snapshot_for_audit(rollback_id)
        root_hash = hash_workspace_root(str(Path(workspace_root).expanduser().resolve()))
        self._record_audit(
            "rollback_restore_requested",
            snapshot,
            restore_id=None,
            files_restored=0,
            files_removed=0,
            files_failed=0,
            workspace_root_hash=root_hash,
        )
        if not self.is_enabled():
            self._record_audit(
                "rollback_restore_blocked",
                snapshot,
                restore_id=None,
                files_restored=0,
                files_removed=0,
                files_failed=0,
                workspace_root_hash=root_hash,
            )
            raise RollbackRestoreApplyError(ROLLBACK_RESTORE_DISABLED_MESSAGE, {"feature_flag": ROLLBACK_RESTORE_FLAG})
        if confirmation != "RESTORE":
            self._record_audit(
                "rollback_restore_blocked",
                snapshot,
                restore_id=None,
                files_restored=0,
                files_removed=0,
                files_failed=0,
                workspace_root_hash=root_hash,
            )
            raise RollbackRestoreApplyError("Rollback restore requires exact confirmation RESTORE.", {"rollback_id": rollback_id})

        self._record_audit(
            "rollback_restore_confirmed",
            snapshot,
            restore_id=None,
            files_restored=0,
            files_removed=0,
            files_failed=0,
            workspace_root_hash=root_hash,
        )
        preflight = self.restore_preflight_service.preflight_restore(rollback_id, workspace_root=workspace_root)
        if not preflight.can_restore:
            self._record_audit(
                "rollback_restore_blocked",
                snapshot,
                restore_id=None,
                files_restored=0,
                files_removed=0,
                files_failed=0,
                workspace_root_hash=root_hash,
            )
            raise RollbackRestoreApplyError(
                "Rollback restore preflight failed.",
                {"rollback_id": rollback_id, "conflict_count": len(preflight.conflicts)},
            )

        snapshot = self.rollback_service.get_snapshot(rollback_id)
        root = self._resolve_workspace(workspace_root, snapshot)
        restore_id = f"restore-{uuid.uuid4().hex}"
        started_at = datetime.now(timezone.utc)
        self._record_audit(
            "rollback_restore_started",
            snapshot,
            restore_id=restore_id,
            files_restored=0,
            files_removed=0,
            files_failed=0,
            workspace_root_hash=root_hash,
        )

        results: list[RollbackRestoreFileResult] = []
        for item in snapshot.files:
            try:
                results.append(self._restore_entry(snapshot, root, item))
            except Exception as exc:
                results.append(
                    RollbackRestoreFileResult(
                        path=item.path,
                        operation="skip",
                        status="failed",
                        expected_hash=item.previous_hash,
                        actual_hash=None,
                        message=f"Restore failed: {type(exc).__name__}.",
                    )
                )

        files_restored = sum(1 for item in results if item.operation == "restore_file" and item.status == "success")
        files_removed = sum(1 for item in results if item.operation == "remove_created_file" and item.status == "success")
        files_failed = sum(1 for item in results if item.status == "failed")
        completed_at = datetime.now(timezone.utc)
        result = RollbackRestoreResult(
            restore_id=restore_id,
            rollback_id=snapshot.rollback_id,
            patch_id=snapshot.patch_id,
            review_id=snapshot.review_id,
            provider_id=snapshot.provider_id,
            status="restored" if files_failed == 0 else "failed",
            started_at=started_at,
            completed_at=completed_at,
            files_restored=files_restored,
            files_removed=files_removed,
            files_failed=files_failed,
            restore_enabled=True,
            apply_enabled=False,
            results=tuple(results),
        )
        self._persist_result(result)
        self._record_audit(
            "rollback_restore_completed" if files_failed == 0 else "rollback_restore_failed",
            snapshot,
            restore_id=restore_id,
            files_restored=files_restored,
            files_removed=files_removed,
            files_failed=files_failed,
            workspace_root_hash=root_hash,
        )
        if files_failed:
            raise RollbackRestoreApplyError("Rollback restore failed for one or more files.", {"restore_id": restore_id, "files_failed": files_failed})
        return result

    def list_restores(self) -> list[RollbackRestoreResult]:
        results: list[RollbackRestoreResult] = []
        if not self.restore_directory.exists():
            return []
        for metadata_path in self.restore_directory.glob("*/metadata.json"):
            try:
                results.append(rollback_restore_result_from_dict(json.loads(metadata_path.read_text(encoding="utf-8"))))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(results, key=lambda item: item.completed_at, reverse=True)

    def get_restore(self, restore_id: str) -> RollbackRestoreResult:
        metadata_path = self._restore_dir(restore_id) / "metadata.json"
        if not metadata_path.exists():
            raise RollbackRestoreApplyError("Rollback restore result was not found.", {"restore_id": restore_id})
        return rollback_restore_result_from_dict(json.loads(metadata_path.read_text(encoding="utf-8")))

    def _restore_entry(self, snapshot: RollbackSnapshot, root: Path, item: RollbackFileBackup) -> RollbackRestoreFileResult:
        target = self._safe_target(root, item.path)
        if item.change_type in {"modify", "delete"}:
            backup = self._verified_backup(snapshot, item)
            self._restore_file(target, backup)
            actual_hash = hash_file(target)
            if item.previous_hash and actual_hash != item.previous_hash:
                return RollbackRestoreFileResult(item.path, "restore_file", "failed", item.previous_hash, actual_hash, "Restored file hash did not match backup metadata.")
            return RollbackRestoreFileResult(item.path, "restore_file", "success", item.previous_hash, actual_hash, "File restored from rollback backup.")

        if item.change_type == "create" and not item.existed_before:
            if not target.exists():
                return RollbackRestoreFileResult(item.path, "remove_created_file", "skipped", None, None, "Created file was already absent.")
            if target.is_dir() or target.is_symlink():
                return RollbackRestoreFileResult(item.path, "remove_created_file", "failed", None, None, "Created rollback target is not a regular file.")
            target.unlink()
            return RollbackRestoreFileResult(item.path, "remove_created_file", "success", None, None, "Created file removed.")

        return RollbackRestoreFileResult(item.path, "skip", "failed", item.previous_hash, None, "Rollback entry is not supported.")

    def _restore_file(self, target: Path, backup: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.parent / f".{target.name}.forgex-restore-{uuid.uuid4().hex}.tmp"
        try:
            with backup.open("rb") as source, temp.open("wb") as destination:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(chunk)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()

    def _verified_backup(self, snapshot: RollbackSnapshot, item: RollbackFileBackup) -> Path:
        if not item.backup_path:
            raise RollbackRestoreApplyError("Rollback backup path is missing.", {"path": item.path})
        snapshot_dir = self.rollback_service._snapshot_dir(snapshot.rollback_id)
        backup = (snapshot_dir / item.backup_path).resolve()
        try:
            backup.relative_to(snapshot_dir.resolve())
        except ValueError as exc:
            raise RollbackRestoreApplyError("Rollback backup path escaped snapshot storage.", {"path": item.path}) from exc
        if not backup.exists() or not backup.is_file() or backup.is_symlink():
            raise RollbackRestoreApplyError("Rollback backup file is missing.", {"path": item.path})
        if item.previous_hash and hash_file(backup) != item.previous_hash:
            raise RollbackRestoreApplyError("Rollback backup hash does not match metadata.", {"path": item.path})
        return backup

    def _safe_target(self, root: Path, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        if not is_safe_relative_path(normalized):
            raise RollbackRestoreApplyError("Rollback restore path is unsafe.", {"path": relative_path})
        if contains_ignored_path_part(normalized):
            raise RollbackRestoreApplyError("Rollback restore path targets an ignored folder.", {"path": relative_path})
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RollbackRestoreApplyError("Rollback restore path escaped workspace root.", {"path": relative_path}) from exc
        return target

    def _resolve_workspace(self, workspace_root: str | Path, snapshot: RollbackSnapshot) -> Path:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RollbackRestoreApplyError("Active workspace root is missing.", {"rollback_id": snapshot.rollback_id})
        if snapshot.workspace_root_hash and snapshot.workspace_root_hash != hash_workspace_root(str(root)):
            raise RollbackRestoreApplyError("Active workspace does not match rollback snapshot.", {"rollback_id": snapshot.rollback_id})
        return root

    def _snapshot_for_audit(self, rollback_id: str) -> RollbackSnapshot:
        try:
            return self.rollback_service.get_snapshot(rollback_id)
        except RollbackSnapshotError as exc:
            raise RollbackRestoreApplyError("Rollback snapshot was not found.", {"rollback_id": rollback_id}) from exc

    def _persist_result(self, result: RollbackRestoreResult) -> None:
        restore_dir = self._restore_dir(result.restore_id)
        restore_dir.mkdir(parents=True, exist_ok=False)
        (restore_dir / "metadata.json").write_text(
            json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _restore_dir(self, restore_id: str) -> Path:
        if "/" in restore_id or "\\" in restore_id or restore_id in {"", ".", ".."}:
            raise RollbackRestoreApplyError("Rollback restore ID is invalid.", {"restore_id": restore_id})
        path = (self.restore_directory / restore_id).resolve()
        try:
            path.relative_to(self.restore_directory.resolve())
        except ValueError as exc:
            raise RollbackRestoreApplyError("Rollback restore path escaped state directory.", {"restore_id": restore_id}) from exc
        return path

    def _record_audit(
        self,
        event: str,
        snapshot: RollbackSnapshot,
        *,
        restore_id: str | None,
        files_restored: int,
        files_removed: int,
        files_failed: int,
        workspace_root_hash: str,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=snapshot.provider_id,
                workspace_root_hash=workspace_root_hash,
                changed_file_count=len(snapshot.files),
                approved=False,
                review_id=snapshot.review_id,
                metadata={
                    "restore_id": restore_id,
                    "rollback_id": snapshot.rollback_id,
                    "patch_id": snapshot.patch_id,
                    "review_id": snapshot.review_id,
                    "files_restored": files_restored,
                    "files_removed": files_removed,
                    "files_failed": files_failed,
                    "workspace_root_hash": workspace_root_hash,
                },
            )
        )
