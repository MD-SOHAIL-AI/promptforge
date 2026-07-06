"""Read-only preflight checks for future rollback restore."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import contains_ignored_path_part, hash_file, is_safe_relative_path
from .review_models import BridgeAuditEntry
from .rollback_models import RollbackFileBackup, RollbackSnapshot, rollback_snapshot_from_dict
from .rollback_restore_models import RollbackRestoreConflict, RollbackRestorePreflightResult
from .rollback_service import RollbackSnapshotError, RollbackSnapshotService


class RollbackRestorePreflightError(ValueError):
    code = "ROLLBACK_RESTORE_PREFLIGHT_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class RollbackRestorePreflightService:
    def __init__(
        self,
        *,
        rollback_service: RollbackSnapshotService,
        audit_log: BridgeAuditLog | None = None,
    ) -> None:
        self.rollback_service = rollback_service
        self.audit_log = audit_log

    def preflight_restore(self, rollback_id: str, *, workspace_root: str | Path) -> RollbackRestorePreflightResult:
        checked_at = datetime.now(timezone.utc)
        root_hash = self._workspace_hash(workspace_root)
        try:
            snapshot, metadata_missing = self._load_snapshot_for_preflight(rollback_id)
        except RollbackSnapshotError as exc:
            raise RollbackRestorePreflightError(str(exc), exc.details) from exc

        self._record_audit("rollback_restore_preflight_started", snapshot, can_restore=False, conflict_count=0, warning_count=0, workspace_root_hash=root_hash)
        conflicts: list[RollbackRestoreConflict] = []
        warnings: list[RollbackRestoreConflict] = []
        files_to_restore: list[str] = []
        files_to_remove: list[str] = []
        workspace_status = "unknown"
        snapshot_status = "valid"

        if metadata_missing:
            snapshot_status = "metadata_missing"
            conflicts.append(conflict("", "metadata_missing", "Rollback metadata is missing."))

        root = self._resolve_workspace(workspace_root, conflicts)
        if root is None:
            workspace_status = "missing"
        else:
            workspace_status = "ok"
            actual_hash = hash_workspace_root(str(root))
            if snapshot.workspace_root_hash and snapshot.workspace_root_hash != actual_hash:
                workspace_status = "hash_mismatch"
                conflicts.append(conflict("", "workspace_hash_mismatch", "Active workspace does not match the rollback snapshot workspace."))

            snapshot_dir = self.rollback_service._snapshot_dir(snapshot.rollback_id)
            for item in snapshot.files:
                if item.change_type in {"modify", "delete"}:
                    files_to_restore.append(item.path)
                elif item.change_type == "create":
                    files_to_remove.append(item.path)
                self._check_entry(snapshot, snapshot_dir, root, item, conflicts, warnings)

        can_restore = not any(item.severity == "error" for item in conflicts)
        result = RollbackRestorePreflightResult(
            rollback_id=snapshot.rollback_id,
            patch_id=snapshot.patch_id,
            review_id=snapshot.review_id,
            provider_id=snapshot.provider_id,
            can_restore=can_restore,
            restore_enabled=False,
            workspace_status=workspace_status,
            snapshot_status=snapshot_status,
            conflicts=tuple(conflicts),
            warnings=tuple(warnings),
            files_to_restore=tuple(files_to_restore),
            files_to_remove=tuple(files_to_remove),
            checked_at=checked_at,
        )
        self._record_audit(
            "rollback_restore_preflight_completed" if result.can_restore else "rollback_restore_preflight_failed",
            snapshot,
            can_restore=result.can_restore,
            conflict_count=len(result.conflicts),
            warning_count=len(result.warnings),
            workspace_root_hash=root_hash,
        )
        return result

    def _load_snapshot_for_preflight(self, rollback_id: str) -> tuple[RollbackSnapshot, bool]:
        snapshot_dir = self.rollback_service._snapshot_dir(rollback_id)
        if not snapshot_dir.exists():
            raise RollbackSnapshotError("Rollback snapshot was not found.", {"rollback_id": rollback_id, "type": "snapshot_missing"})
        metadata_path = snapshot_dir / "metadata.json"
        if not metadata_path.exists():
            return (
                RollbackSnapshot(
                    rollback_id=rollback_id,
                    patch_id="",
                    review_id="",
                    provider_id="unknown",
                    workspace_root_hash="",
                    created_at=datetime.now(timezone.utc),
                    status="created",
                    files=(),
                    total_bytes=0,
                    apply_id=None,
                    restore_enabled=False,
                ),
                True,
            )
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            return rollback_snapshot_from_dict(payload), False
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RollbackRestorePreflightError("Rollback metadata could not be read.", {"rollback_id": rollback_id}) from exc

    def _resolve_workspace(self, workspace_root: str | Path, conflicts: list[RollbackRestoreConflict]) -> Path | None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            conflicts.append(conflict("", "workspace_missing", "Active workspace root is missing."))
            return None
        return root

    def _check_entry(
        self,
        snapshot: RollbackSnapshot,
        snapshot_dir: Path,
        root: Path,
        item: RollbackFileBackup,
        conflicts: list[RollbackRestoreConflict],
        warnings: list[RollbackRestoreConflict],
    ) -> None:
        target = self._safe_workspace_target(root, item.path, conflicts)
        if target is None:
            return
        self._check_backup(snapshot_dir, item, conflicts)
        if item.change_type == "modify":
            if not target.exists():
                conflicts.append(conflict(item.path, "current_file_missing", "Current file is missing."))
            elif snapshot.apply_id:
                if item.post_apply_hash and hash_file(target) != item.post_apply_hash:
                    conflicts.append(conflict(item.path, "current_file_changed", "Current file changed after patch apply."))
                elif not item.post_apply_hash:
                    warnings.append(warning(item.path, "restore_unsupported", "Applied-file hash is unavailable for this rollback entry."))
            elif item.previous_hash and hash_file(target) != item.previous_hash:
                conflicts.append(conflict(item.path, "current_file_changed", "Current file changed after rollback snapshot was created."))
        elif item.change_type == "delete":
            if snapshot.apply_id:
                if target.exists():
                    conflicts.append(conflict(item.path, "delete_target_changed", "Deleted-file rollback target exists after patch apply."))
            elif target.exists() and item.previous_hash and hash_file(target) != item.previous_hash:
                conflicts.append(conflict(item.path, "delete_target_changed", "Delete restore target changed after rollback snapshot was created."))
        elif item.change_type == "create":
            if not target.exists():
                warnings.append(warning(item.path, "current_file_missing", "Created-file rollback target is already missing."))
            elif snapshot.apply_id and item.post_apply_hash and hash_file(target) != item.post_apply_hash:
                conflicts.append(conflict(item.path, "created_file_changed", "Created-file rollback target changed after patch apply."))
            elif not snapshot.apply_id and item.previous_hash and hash_file(target) != item.previous_hash:
                conflicts.append(conflict(item.path, "created_file_changed", "Created-file rollback target changed after rollback snapshot was created."))
            elif not item.previous_hash and not (snapshot.apply_id and item.post_apply_hash):
                warnings.append(warning(item.path, "restore_unsupported", "Created-file rollback has no post-apply hash to verify."))
        else:
            conflicts.append(conflict(item.path, "restore_unsupported", "Rollback change type is not supported."))

    def _check_backup(self, snapshot_dir: Path, item: RollbackFileBackup, conflicts: list[RollbackRestoreConflict]) -> None:
        if item.change_type == "create" and not item.existed_before:
            return
        if not item.backup_path:
            conflicts.append(conflict(item.path, "backup_missing", "Rollback backup path is missing."))
            return
        backup = (snapshot_dir / item.backup_path).resolve()
        try:
            backup.relative_to(snapshot_dir.resolve())
        except ValueError:
            conflicts.append(conflict(item.path, "path_unsafe", "Rollback backup path escapes snapshot storage."))
            return
        if not backup.exists() or not backup.is_file():
            conflicts.append(conflict(item.path, "backup_missing", "Rollback backup file is missing."))
            return
        if item.previous_hash and hash_file(backup) != item.previous_hash:
            conflicts.append(conflict(item.path, "backup_modified", "Rollback backup file no longer matches metadata."))

    def _safe_workspace_target(
        self,
        root: Path,
        relative_path: str,
        conflicts: list[RollbackRestoreConflict],
    ) -> Path | None:
        normalized = relative_path.replace("\\", "/")
        if not is_safe_relative_path(normalized):
            conflicts.append(conflict(relative_path, "path_unsafe", "Rollback path is unsafe."))
            return None
        if contains_ignored_path_part(normalized):
            conflicts.append(conflict(relative_path, "ignored_path", "Rollback path targets an ignored folder."))
            return None
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            conflicts.append(conflict(relative_path, "symlink_escape", "Rollback path follows a symlink outside the workspace."))
            return None
        return target

    def _workspace_hash(self, workspace_root: str | Path) -> str:
        return hash_workspace_root(str(Path(workspace_root).expanduser().resolve()))

    def _record_audit(
        self,
        event: str,
        snapshot: RollbackSnapshot,
        *,
        can_restore: bool,
        conflict_count: int,
        warning_count: int,
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
                    "rollback_id": snapshot.rollback_id,
                    "patch_id": snapshot.patch_id,
                    "review_id": snapshot.review_id,
                    "can_restore": can_restore,
                    "conflict_count": conflict_count,
                    "warning_count": warning_count,
                    "workspace_root_hash": workspace_root_hash,
                },
            )
        )


def conflict(path: str, conflict_type: str, message: str) -> RollbackRestoreConflict:
    return RollbackRestoreConflict(path=path, type=conflict_type, severity="error", message=message)  # type: ignore[arg-type]


def warning(path: str, conflict_type: str, message: str) -> RollbackRestoreConflict:
    return RollbackRestoreConflict(path=path, type=conflict_type, severity="warning", message=message)  # type: ignore[arg-type]
