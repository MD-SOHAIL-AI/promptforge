"""Rollback snapshot storage for future patch apply."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import contains_ignored_path_part, hash_file, is_safe_relative_path
from .patch_preflight_service import PatchPreflightService
from .preflight_models import PatchPreflightResult
from .review_models import BridgeAuditEntry
from .rollback_models import RollbackFileBackup, RollbackSnapshot, rollback_snapshot_from_dict


class RollbackSnapshotError(ValueError):
    code = "ROLLBACK_SNAPSHOT_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class RollbackSnapshotService:
    def __init__(
        self,
        *,
        rollback_directory: str | Path,
        preflight_service: PatchPreflightService,
        audit_log: BridgeAuditLog | None = None,
    ) -> None:
        self.rollback_directory = Path(rollback_directory)
        self.preflight_service = preflight_service
        self.audit_log = audit_log

    def create_snapshot(self, patch_id: str, *, workspace_root: str | Path) -> RollbackSnapshot:
        preflight = self.preflight_service.preflight_patch(patch_id, workspace_root=workspace_root)
        requested_root_hash = hash_workspace_root(str(Path(workspace_root).expanduser().resolve()))
        self._record_audit("rollback_snapshot_requested", preflight, rollback_id=None, files_backed_up=0, workspace_root_hash=requested_root_hash)
        try:
            if not preflight.can_apply:
                raise RollbackSnapshotError(
                    "Rollback snapshot requires a passing patch preflight.",
                    {"patch_id": patch_id, "conflict_count": len(preflight.conflicts)},
                )
            root = self._resolve_workspace_root(workspace_root, preflight)
            rollback_id = f"rollback-{uuid.uuid4().hex}"
            snapshot_dir = self._snapshot_dir(rollback_id)
            files_dir = snapshot_dir / "files"
            files: list[RollbackFileBackup] = []
            total_bytes = 0
            for path in preflight.files_to_create:
                backup = self._backup_file(root, files_dir, path, "create")
                files.append(backup)
                total_bytes += backup.size
            for path in preflight.files_to_modify:
                backup = self._backup_file(root, files_dir, path, "modify")
                files.append(backup)
                total_bytes += backup.size
            for path in preflight.files_to_delete:
                backup = self._backup_file(root, files_dir, path, "delete")
                files.append(backup)
                total_bytes += backup.size

            snapshot = RollbackSnapshot(
                rollback_id=rollback_id,
                patch_id=preflight.patch_id,
                review_id=preflight.review_id,
                provider_id=preflight.provider_id,
                workspace_root_hash=hash_workspace_root(str(root)),
                created_at=datetime.now(timezone.utc),
                status="created",
                files=tuple(files),
                total_bytes=total_bytes,
                apply_id=None,
                restore_enabled=False,
            )
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / "metadata.json").write_text(
                json.dumps(snapshot.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self._record_audit(
                "rollback_snapshot_created",
                preflight,
                rollback_id=rollback_id,
                files_backed_up=sum(1 for item in files if item.existed_before and item.backup_path),
                workspace_root_hash=snapshot.workspace_root_hash,
            )
            return snapshot
        except Exception:
            self._record_audit("rollback_snapshot_failed", preflight, rollback_id=None, files_backed_up=0, workspace_root_hash=requested_root_hash)
            raise

    def list_snapshots(self) -> list[RollbackSnapshot]:
        snapshots: list[RollbackSnapshot] = []
        if not self.rollback_directory.exists():
            return []
        for metadata_path in self.rollback_directory.glob("*/metadata.json"):
            try:
                snapshots.append(self._load_metadata(metadata_path))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(snapshots, key=lambda item: item.created_at, reverse=True)

    def get_snapshot(self, rollback_id: str) -> RollbackSnapshot:
        snapshot_dir = self._snapshot_dir(rollback_id)
        metadata_path = snapshot_dir / "metadata.json"
        if not metadata_path.exists():
            raise RollbackSnapshotError("Rollback snapshot was not found.", {"rollback_id": rollback_id})
        return self._load_metadata(metadata_path)

    def delete_snapshot(self, rollback_id: str) -> RollbackSnapshot:
        snapshot = self.get_snapshot(rollback_id)
        snapshot_dir = self._snapshot_dir(rollback_id)
        shutil.rmtree(snapshot_dir)
        self._record_snapshot_audit("rollback_snapshot_deleted", snapshot)
        return snapshot

    def cleanup_snapshots(self, *, older_than_days: int = 30) -> dict[str, Any]:
        if older_than_days < 0:
            raise RollbackSnapshotError("Rollback cleanup age must be zero or greater.", {"older_than_days": older_than_days})
        self._record_simple_audit("rollback_snapshot_cleanup_started", {"older_than_days": older_than_days})
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        removed: list[RollbackSnapshot] = []
        for snapshot in self.list_snapshots():
            if snapshot.created_at >= cutoff:
                continue
            removed.append(self.delete_snapshot(snapshot.rollback_id))
        self._record_simple_audit("rollback_snapshot_cleanup_completed", {"older_than_days": older_than_days, "removed": len(removed)})
        return {"removed": len(removed), "snapshots": [item.to_dict() for item in removed]}

    def _backup_file(self, root: Path, files_dir: Path, relative_path: str, change_type: str) -> RollbackFileBackup:
        normalized = self._safe_relative_path(root, relative_path)
        target = (root / normalized).resolve()
        existed = target.exists()
        if change_type in {"modify", "delete"} and not existed:
            raise RollbackSnapshotError("Rollback target file is missing.", {"path": normalized})
        if change_type == "create" and not existed:
            return RollbackFileBackup(
                path=normalized,
                change_type="create",
                existed_before=False,
                previous_hash=None,
                backup_path=None,
                size=0,
                mtime=None,
            )
        if not target.is_file() or target.is_symlink():
            raise RollbackSnapshotError("Rollback target must be a regular file.", {"path": normalized})
        stat = target.stat()
        backup_path = (files_dir / normalized).resolve()
        try:
            backup_path.relative_to(files_dir.resolve())
        except ValueError as exc:
            raise RollbackSnapshotError("Rollback backup path escaped snapshot directory.", {"path": normalized}) from exc
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        return RollbackFileBackup(
            path=normalized,
            change_type=change_type,  # type: ignore[arg-type]
            existed_before=True,
            previous_hash=hash_file(target),
            backup_path=backup_path.relative_to(self._snapshot_dir_from_files_dir(files_dir)).as_posix(),
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def _safe_relative_path(self, root: Path, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/")
        if not is_safe_relative_path(normalized):
            raise RollbackSnapshotError("Rollback path is unsafe.", {"path": relative_path})
        if contains_ignored_path_part(normalized):
            raise RollbackSnapshotError("Rollback path targets an ignored folder.", {"path": relative_path})
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RollbackSnapshotError("Rollback path escaped workspace root.", {"path": relative_path}) from exc
        return normalized

    def _resolve_workspace_root(self, workspace_root: str | Path, preflight: PatchPreflightResult) -> Path:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise RollbackSnapshotError("Active workspace root is missing.", {"patch_id": preflight.patch_id})
        if preflight.workspace_status != "ok":
            raise RollbackSnapshotError("Active workspace failed preflight workspace checks.", {"workspace_status": preflight.workspace_status})
        return root

    def _snapshot_dir(self, rollback_id: str) -> Path:
        if "/" in rollback_id or "\\" in rollback_id or rollback_id in {"", ".", ".."}:
            raise RollbackSnapshotError("Rollback snapshot ID is invalid.", {"rollback_id": rollback_id})
        path = (self.rollback_directory / rollback_id).resolve()
        try:
            path.relative_to(self.rollback_directory.resolve())
        except ValueError as exc:
            raise RollbackSnapshotError("Rollback snapshot path escaped state directory.", {"rollback_id": rollback_id}) from exc
        return path

    def _snapshot_dir_from_files_dir(self, files_dir: Path) -> Path:
        return files_dir.parent

    def _load_metadata(self, metadata_path: Path) -> RollbackSnapshot:
        snapshot = rollback_snapshot_from_dict(json.loads(metadata_path.read_text(encoding="utf-8")))
        self._snapshot_dir(snapshot.rollback_id)
        return snapshot

    def _record_audit(
        self,
        event: str,
        preflight: PatchPreflightResult,
        *,
        rollback_id: str | None,
        files_backed_up: int,
        workspace_root_hash: str,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=preflight.provider_id,
                workspace_root_hash=workspace_root_hash,
                changed_file_count=len(preflight.files_to_create) + len(preflight.files_to_modify) + len(preflight.files_to_delete),
                approved=False,
                review_id=preflight.review_id,
                metadata={
                    "rollback_id": rollback_id,
                    "patch_id": preflight.patch_id,
                    "review_id": preflight.review_id,
                    "provider_id": preflight.provider_id,
                    "files_backed_up": files_backed_up,
                    "workspace_root_hash": workspace_root_hash,
                },
            )
        )

    def _record_snapshot_audit(self, event: str, snapshot: RollbackSnapshot) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=snapshot.provider_id,
                workspace_root_hash=snapshot.workspace_root_hash,
                changed_file_count=len(snapshot.files),
                approved=False,
                review_id=snapshot.review_id,
                metadata={
                    "rollback_id": snapshot.rollback_id,
                    "patch_id": snapshot.patch_id,
                    "review_id": snapshot.review_id,
                    "provider_id": snapshot.provider_id,
                    "files_backed_up": sum(1 for item in snapshot.files if item.existed_before and item.backup_path),
                    "workspace_root_hash": snapshot.workspace_root_hash,
                },
            )
        )

    def _record_simple_audit(self, event: str, metadata: dict[str, Any]) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id="all",
                workspace_root_hash="",
                changed_file_count=0,
                approved=False,
                metadata=metadata,
            )
        )
