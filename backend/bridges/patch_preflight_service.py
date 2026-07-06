"""Read-only safety preflight for exported bridge patches."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import contains_ignored_path_part, hash_file, is_safe_relative_path
from .patch_export_service import sha256_file
from .patch_store import BridgePatchRecord, BridgePatchStore, BridgePatchStoreError
from .preflight_models import PatchPreflightConflict, PatchPreflightResult
from .review_models import BridgeAuditEntry, BridgeChangedFile, BridgeReviewSession

PATCH_APPLY_FLAG = "FORGEX_ENABLE_PATCH_APPLY"
ROLLBACK_RESTORE_FLAG = "FORGEX_ENABLE_ROLLBACK_RESTORE"


class PatchPreflightError(ValueError):
    code = "PATCH_PREFLIGHT_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class ParsedPatchOperations:
    paths: tuple[str, ...]
    files_to_create: tuple[str, ...]
    files_to_modify: tuple[str, ...]
    files_to_delete: tuple[str, ...]


class PatchPreflightService:
    def __init__(
        self,
        *,
        patch_store: BridgePatchStore,
        review_service: Any,
        audit_log: BridgeAuditLog | None = None,
    ) -> None:
        self.patch_store = patch_store
        self.review_service = review_service
        self.audit_log = audit_log

    def preflight_patch(self, patch_id: str, *, workspace_root: str | Path | None = None) -> PatchPreflightResult:
        checked_at = datetime.now(timezone.utc)
        record = self._load_record(patch_id)
        self._record_audit("patch_preflight_started", record, can_apply=False, conflict_count=0, warning_count=0)
        conflicts: list[PatchPreflightConflict] = []
        warnings: list[PatchPreflightConflict] = []
        integrity_status = "unknown"
        review_status = "unknown"
        workspace_status = "unknown"
        operations = ParsedPatchOperations((), (), (), ())
        patch_text = ""

        try:
            patch_path = self._patch_path(record, conflicts)
            if patch_path is not None and patch_path.exists():
                current_hash = sha256_file(patch_path)
                integrity_status = "valid" if record.patch_sha256 and current_hash == record.patch_sha256 else "modified"
                if integrity_status != "valid":
                    conflicts.append(conflict(record.patch_path, "patch_modified", "Exported patch content no longer matches recorded SHA-256."))
                patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
                operations = self._parse_patch(record, patch_text, conflicts)
            else:
                integrity_status = "missing"
                conflicts.append(conflict(record.patch_path, "patch_missing", "Exported patch file is missing."))

            review = self._load_review(record, conflicts)
            if review is not None:
                review_status = self._check_review(record, review, conflicts)
            root = self._resolve_workspace_root(record, review, workspace_root, conflicts)
            if root is not None:
                workspace_status = "ok"
                self._check_workspace_identity(record, root, conflicts)
                self._check_paths(root, operations.paths, conflicts)
                if review is not None:
                    self._check_drift(root, review, operations, conflicts, warnings)
            else:
                workspace_status = "missing"

            if patch_text and "# ... [patch truncated]" in patch_text:
                conflicts.append(conflict(record.patch_path, "parse_error", "Patch export is truncated and cannot be simulated safely."))

            result = PatchPreflightResult(
                patch_id=record.patch_id,
                review_id=record.review_id,
                provider_id=record.provider_id,
                can_apply=not any(item.severity == "error" for item in conflicts),
                apply_enabled=self._apply_enabled(),
                integrity_status=integrity_status,
                review_status=review_status,
                workspace_status=workspace_status,
                conflicts=tuple(conflicts),
                warnings=tuple(warnings),
                files_to_create=operations.files_to_create,
                files_to_modify=operations.files_to_modify,
                files_to_delete=operations.files_to_delete,
                checked_at=checked_at,
            )
            self._record_audit(
                "patch_preflight_completed" if result.can_apply else "patch_preflight_failed",
                record,
                can_apply=result.can_apply,
                conflict_count=len(result.conflicts),
                warning_count=len(result.warnings),
            )
            return result
        except Exception as exc:
            if not conflicts:
                conflicts.append(conflict(record.patch_path, "unknown", f"Preflight failed: {type(exc).__name__}."))
            result = PatchPreflightResult(
                patch_id=record.patch_id,
                review_id=record.review_id,
                provider_id=record.provider_id,
                can_apply=False,
                apply_enabled=self._apply_enabled(),
                integrity_status=integrity_status,
                review_status=review_status,
                workspace_status=workspace_status,
                conflicts=tuple(conflicts),
                warnings=tuple(warnings),
                files_to_create=operations.files_to_create,
                files_to_modify=operations.files_to_modify,
                files_to_delete=operations.files_to_delete,
                checked_at=checked_at,
            )
            self._record_audit(
                "patch_preflight_failed",
                record,
                can_apply=False,
                conflict_count=len(result.conflicts),
                warning_count=len(result.warnings),
            )
            return result

    def _load_record(self, patch_id: str) -> BridgePatchRecord:
        try:
            return self.patch_store.get(patch_id)
        except BridgePatchStoreError as exc:
            raise PatchPreflightError("Patch record was not found.", {"patch_id": patch_id}) from exc

    def _patch_path(self, record: BridgePatchRecord, conflicts: list[PatchPreflightConflict]) -> Path | None:
        try:
            return self.patch_store.safe_child(record.patch_path)
        except BridgePatchStoreError:
            conflicts.append(conflict(record.patch_path, "path_unsafe", "Patch record path is not managed by ForgeX."))
            return None

    def _load_review(self, record: BridgePatchRecord, conflicts: list[PatchPreflightConflict]) -> BridgeReviewSession | None:
        sessions = getattr(self.review_service, "_sessions", {})
        review = sessions.get(record.review_id) if isinstance(sessions, dict) else None
        if review is None:
            conflicts.append(conflict("", "review_missing", "Bridge review was not found."))
            return None
        return review

    def _check_review(
        self,
        record: BridgePatchRecord,
        review: BridgeReviewSession,
        conflicts: list[PatchPreflightConflict],
    ) -> str:
        status = "expired" if datetime.now(timezone.utc) > review.expires_at else review.status
        if status == "expired":
            conflicts.append(conflict("", "review_expired", "Bridge review is expired."))
        elif status != "approved":
            conflicts.append(conflict("", "review_not_approved", "Bridge review must be approved before apply preflight can pass."))
        if review.provider_id != record.provider_id:
            conflicts.append(conflict("", "provider_mismatch", "Patch provider does not match review provider."))

        review_paths = {item.path for item in review.changed_files}
        record_paths = set(record.created_files) | set(record.modified_files) | set(record.deleted_files)
        if review_paths != record_paths:
            conflicts.append(conflict("", "patch_modified", "Patch changed-file metadata does not match the review."))
        return status

    def _resolve_workspace_root(
        self,
        record: BridgePatchRecord,
        review: BridgeReviewSession | None,
        workspace_root: str | Path | None,
        conflicts: list[PatchPreflightConflict],
    ) -> Path | None:
        del record
        candidate = str(workspace_root or (review.workspace_root if review is not None else "")).strip()
        if not candidate:
            conflicts.append(conflict("", "workspace_drift", "Active workspace root is required for preflight."))
            return None
        root = Path(candidate).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            conflicts.append(conflict("", "workspace_drift", "Active workspace root is missing."))
            return None
        if not os.access(root, os.R_OK | os.W_OK):
            conflicts.append(conflict("", "workspace_drift", "Active workspace root is not readable and writable."))
            return None
        return root

    def _check_workspace_identity(
        self,
        record: BridgePatchRecord,
        root: Path,
        conflicts: list[PatchPreflightConflict],
    ) -> None:
        expected = record.workspace_root_hash
        actual = hash_workspace_root(str(root))
        if expected and expected != actual:
            conflicts.append(conflict("", "workspace_drift", "Active workspace root does not match the patch review workspace."))

    def _parse_patch(
        self,
        record: BridgePatchRecord,
        patch_text: str,
        conflicts: list[PatchPreflightConflict],
    ) -> ParsedPatchOperations:
        if not patch_text.strip() or "diff --git " not in patch_text:
            conflicts.append(conflict(record.patch_path, "parse_error", "Patch does not contain supported git diff headers."))
            return ParsedPatchOperations((), (), (), ())

        parsed_paths: list[str] = []
        for line in patch_text.splitlines():
            if not line.startswith("diff --git "):
                continue
            match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
            if not match:
                conflicts.append(conflict("", "parse_error", "Patch contains an unsupported diff header."))
                continue
            left, right = match.group(1), match.group(2)
            if left != right:
                conflicts.append(conflict(right, "parse_error", "Patch rename or path mismatch is not supported by preflight."))
            parsed_paths.append(right)

        metadata_paths = list(record.created_files) + list(record.modified_files) + list(record.deleted_files)
        if sorted(set(parsed_paths)) != sorted(set(metadata_paths)):
            conflicts.append(conflict("", "parse_error", "Patch file paths do not match patch metadata."))
        if "# Binary or unsupported diff preview" in patch_text:
            for path in parsed_paths or metadata_paths:
                conflicts.append(conflict(path, "binary_unsupported", "Binary or unsupported patch content cannot be simulated."))

        return ParsedPatchOperations(
            paths=tuple(dict.fromkeys(metadata_paths)),
            files_to_create=record.created_files,
            files_to_modify=record.modified_files,
            files_to_delete=record.deleted_files,
        )

    def _check_paths(self, root: Path, paths: tuple[str, ...], conflicts: list[PatchPreflightConflict]) -> None:
        for path in paths:
            normalized = path.replace("\\", "/")
            if not is_safe_relative_path(normalized):
                conflicts.append(conflict(path, "path_unsafe", "Patch path must be relative and stay inside the workspace."))
                continue
            if contains_ignored_path_part(normalized):
                conflicts.append(conflict(path, "ignored_path", "Patch path targets an ignored or generated folder."))
                continue
            target = (root / normalized).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                conflicts.append(conflict(path, "path_unsafe", "Patch path escapes the active workspace."))
                continue
            parent = target.parent
            if parent.exists():
                try:
                    parent.resolve().relative_to(root)
                except ValueError:
                    conflicts.append(conflict(path, "path_unsafe", "Patch path follows a symlink outside the workspace."))

    def _check_drift(
        self,
        root: Path,
        review: BridgeReviewSession,
        operations: ParsedPatchOperations,
        conflicts: list[PatchPreflightConflict],
        warnings: list[PatchPreflightConflict],
    ) -> None:
        by_path = {item.path: item for item in review.changed_files}
        for path in operations.files_to_create:
            target = root / path
            if target.exists():
                warnings.append(warning(path, "target_changed", "Patch creates this file, but it already exists in the active workspace."))
        for path in operations.files_to_modify:
            changed = by_path.get(path)
            target = root / path
            if not target.exists():
                conflicts.append(conflict(path, "target_missing", "Patch modifies this file, but it is missing from the active workspace."))
                continue
            self._check_expected_hash(path, target, changed, conflicts, conflict_type="target_changed")
        for path in operations.files_to_delete:
            changed = by_path.get(path)
            target = root / path
            if not target.exists():
                conflicts.append(conflict(path, "target_missing", "Patch deletes this file, but it is already missing from the active workspace."))
                continue
            self._check_expected_hash(path, target, changed, conflicts, conflict_type="delete_conflict")

        operation_paths = set(operations.paths)
        for changed in review.changed_files:
            if changed.path not in operation_paths:
                conflicts.append(conflict(changed.path, "parse_error", "Review changed file is missing from patch operations."))
            if not changed.safe:
                conflicts.append(conflict(changed.path, "path_unsafe", changed.warning or "Review path is unsafe."))
            if not changed.preview_supported:
                conflict_type = "large_file_unsupported" if "large" in (changed.warning or "").casefold() else "binary_unsupported"
                conflicts.append(conflict(changed.path, conflict_type, changed.warning or "Patch preview is unsupported."))

    def _check_expected_hash(
        self,
        path: str,
        target: Path,
        changed: BridgeChangedFile | None,
        conflicts: list[PatchPreflightConflict],
        *,
        conflict_type: str,
    ) -> None:
        expected_hash = changed.previous_hash if changed is not None else None
        if not expected_hash:
            conflicts.append(conflict(path, "unknown", "Review baseline hash is unavailable for this path."))
            return
        if hash_file(target) != expected_hash:
            message = "File changed after review was created."
            if conflict_type == "delete_conflict":
                message = "File changed after review was created; delete cannot be simulated safely."
            conflicts.append(conflict(path, conflict_type, message))

    def _record_audit(
        self,
        event: str,
        record: BridgePatchRecord,
        *,
        can_apply: bool,
        conflict_count: int,
        warning_count: int,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=record.provider_id,
                workspace_root_hash=record.workspace_root_hash,
                changed_file_count=record.changed_file_count,
                approved=False,
                review_id=record.review_id,
                metadata={
                    "patch_id": record.patch_id,
                    "can_apply": can_apply,
                    "conflict_count": conflict_count,
                    "warning_count": warning_count,
                    "workspace_root_hash": record.workspace_root_hash,
                },
            )
        )

    def _apply_enabled(self) -> bool:
        return os.getenv(PATCH_APPLY_FLAG, "").strip() == "1" and os.getenv(ROLLBACK_RESTORE_FLAG, "").strip() == "1"


def conflict(path: str, conflict_type: str, message: str) -> PatchPreflightConflict:
    return PatchPreflightConflict(path=path, type=conflict_type, severity="error", message=message)  # type: ignore[arg-type]


def warning(path: str, conflict_type: str, message: str) -> PatchPreflightConflict:
    return PatchPreflightConflict(path=path, type=conflict_type, severity="warning", message=message)  # type: ignore[arg-type]
