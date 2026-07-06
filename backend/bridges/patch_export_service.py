"""Patch export support for bridge review sessions."""

from __future__ import annotations

import re
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .audit_log import BridgeAuditLog
from .diff_service import BridgeDiffError, BridgeDiffService, is_safe_relative_path
from .patch_store import BridgePatchRecord, BridgePatchStore, BridgePatchStoreError
from .review_models import BridgeAuditEntry, BridgeReviewSession


MAX_PATCH_CHARS = 250_000
PatchIntegrityStatus = Literal["valid", "missing", "modified", "unknown"]


class BridgePatchExportError(ValueError):
    code = "BRIDGE_PATCH_EXPORT_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class BridgePatchExport:
    review_id: str
    patch_id: str
    provider_id: str
    patch_path: str
    patch_size: int
    patch_sha256: str
    file_count: int
    changed_file_count: int
    created_files: tuple[str, ...]
    modified_files: tuple[str, ...]
    deleted_files: tuple[str, ...]
    workspace_root_hash: str
    review_status_at_export: str
    apply_enabled: bool
    integrity_status: PatchIntegrityStatus
    created_at: datetime
    download_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "patch_id": self.patch_id,
            "provider_id": self.provider_id,
            "patch_path": self.patch_path,
            "patch_size": self.patch_size,
            "patch_sha256": self.patch_sha256,
            "file_count": self.file_count,
            "changed_file_count": self.changed_file_count,
            "created_files": list(self.created_files),
            "modified_files": list(self.modified_files),
            "deleted_files": list(self.deleted_files),
            "workspace_root_hash": self.workspace_root_hash,
            "review_status_at_export": self.review_status_at_export,
            "apply_enabled": self.apply_enabled,
            "integrity_status": self.integrity_status,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "download_url": self.download_url,
        }


class BridgePatchExportService:
    def __init__(
        self,
        *,
        review_service: BridgeDiffService,
        patch_directory: str | Path,
        audit_log: BridgeAuditLog | None = None,
        patch_store: BridgePatchStore | None = None,
        opener: Any | None = None,
        max_patch_chars: int = MAX_PATCH_CHARS,
    ) -> None:
        self.review_service = review_service
        self.patch_directory = Path(patch_directory)
        self.audit_log = audit_log
        self.patch_store = patch_store or BridgePatchStore(self.patch_directory)
        self.opener = opener or open_path
        self.max_patch_chars = max_patch_chars

    def export_patch(self, review_id: str) -> BridgePatchExport:
        review = self._get_review(review_id)
        patch_text = self._build_patch(review)
        self.patch_directory.mkdir(parents=True, exist_ok=True)
        patch_id = f"{review.review_id}.patch"
        path = self._patch_path(review.review_id)
        path.write_text(patch_text, encoding="utf-8")
        export = self._metadata_from_review(
            review,
            patch_id=patch_id,
            patch_size=path.stat().st_size,
            patch_sha256=sha256_file(path),
            created_at=datetime.now(timezone.utc),
            integrity_status="valid",
        )
        self._metadata_path(review.review_id).write_text(
            json.dumps(export.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.patch_store.upsert(self._record_from_export(export))
        self._record_audit(
            "bridge_patch_exported",
            review,
            export.patch_size,
            patch_sha256=export.patch_sha256,
            integrity_status=export.integrity_status,
        )
        self._record_audit(
            "bridge_patch_metadata_created",
            review,
            export.patch_size,
            patch_sha256=export.patch_sha256,
            integrity_status=export.integrity_status,
        )
        return export

    def patch_metadata(self, review_id: str) -> BridgePatchExport:
        review = self._get_review(review_id)
        metadata_path = self._metadata_path(review.review_id)
        if not metadata_path.exists():
            patch_path = self._patch_path(review.review_id)
            status = "unknown" if patch_path.exists() else "missing"
            return self._metadata_from_review(
                review,
                patch_id=f"{review.review_id}.patch",
                patch_size=patch_path.stat().st_size if patch_path.exists() else 0,
                patch_sha256=sha256_file(patch_path) if patch_path.exists() else "",
                created_at=datetime.now(timezone.utc),
                integrity_status=status,
            )
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
        return export_from_dict(data)

    def verify_patch(self, review_id: str) -> BridgePatchExport:
        metadata = self.patch_metadata(review_id)
        review = self._get_review(review_id)
        patch_path = self._patch_path(review.review_id)
        if not patch_path.exists():
            status: PatchIntegrityStatus = "missing"
            patch_size = 0
            patch_sha256 = metadata.patch_sha256
        else:
            current_hash = sha256_file(patch_path)
            patch_size = patch_path.stat().st_size
            patch_sha256 = metadata.patch_sha256
            status = "valid" if metadata.patch_sha256 and current_hash == metadata.patch_sha256 else "modified"
        updated = self._metadata_from_review(
            review,
            patch_id=metadata.patch_id,
            patch_size=patch_size,
            patch_sha256=patch_sha256,
            created_at=metadata.created_at,
            integrity_status=status,
        )
        self._write_metadata(updated)
        self._record_audit(
            "bridge_patch_verified",
            review,
            updated.patch_size,
            patch_sha256=updated.patch_sha256,
            integrity_status=status,
        )
        if status in {"modified", "missing"}:
            self._record_audit(
                "bridge_patch_integrity_failed",
                review,
                updated.patch_size,
                patch_sha256=updated.patch_sha256,
                integrity_status=status,
            )
        return updated

    def list_patches(
        self,
        *,
        provider_id: str | None = None,
        review_id: str | None = None,
        integrity_status: str | None = None,
    ) -> list[BridgePatchExport]:
        self._record_history_viewed(provider_id=provider_id, review_id=review_id, integrity_status=integrity_status)
        records = self.patch_store.list_records(
            provider_id=provider_id,
            review_id=review_id,
            integrity_status=integrity_status,
        )
        return [export_from_record(record) for record in records]

    def delete_patch(self, patch_id: str) -> dict[str, Any]:
        try:
            record = self.patch_store.get(patch_id)
        except BridgePatchStoreError as exc:
            raise BridgePatchExportError("Patch record was not found.", {"patch_id": patch_id}) from exc
        patch_path = self.patch_store.safe_child(record.patch_path)
        metadata_path = self.patch_store.safe_child(record.metadata_path)
        if patch_path.exists():
            patch_path.unlink()
        if metadata_path.exists():
            metadata_path.unlink()
        self.patch_store.remove(patch_id)
        self._record_patch_audit("bridge_patch_deleted", record)
        return {"deleted": True, "patch": export_from_record(record).to_dict()}

    def cleanup_patches(self, *, older_than_days: int = 30, include_missing: bool = True) -> dict[str, Any]:
        if older_than_days < 0:
            raise BridgePatchExportError("Patch cleanup age must be zero or greater.", {"older_than_days": older_than_days})
        self._record_simple_audit("bridge_patch_cleanup_started", {"older_than_days": older_than_days, "include_missing": include_missing})
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        removed: list[dict[str, Any]] = []
        try:
            for record in list(self.patch_store.list_records()):
                patch_path = self.patch_store.safe_child(record.patch_path)
                missing = not patch_path.exists()
                old = record.created_at < cutoff
                if not old and not (include_missing and missing):
                    continue
                deleted = self.delete_patch(record.patch_id)
                removed.append(deleted["patch"])
            self._record_simple_audit(
                "bridge_patch_cleanup_completed",
                {"removed": len(removed), "older_than_days": older_than_days, "include_missing": include_missing},
            )
        except Exception as exc:
            self._record_simple_audit(
                "bridge_patch_cleanup_failed",
                {"error": type(exc).__name__, "older_than_days": older_than_days, "include_missing": include_missing},
            )
            raise
        return {"removed": len(removed), "patches": removed}

    def open_patch_folder(self, review_id: str) -> dict[str, Any]:
        review = self._get_review(review_id)
        patch_path = self._patch_path(review.review_id)
        if not patch_path.exists():
            raise BridgePatchExportError("Patch file is missing. Export it before opening the patch folder.", {"review_id": review_id})
        try:
            self.opener(patch_path.parent)
        except OSError as exc:
            raise BridgePatchExportError("Patch folder could not be opened.", {"review_id": review_id}) from exc
        metadata = self.verify_patch(review_id)
        self._record_audit(
            "bridge_patch_folder_opened",
            review,
            metadata.patch_size,
            patch_sha256=metadata.patch_sha256,
            integrity_status=metadata.integrity_status,
        )
        return {"opened": True, "patch_path": metadata.patch_path, "review_id": review.review_id}

    def _metadata_from_review(
        self,
        review: BridgeReviewSession,
        *,
        patch_id: str,
        patch_size: int,
        patch_sha256: str,
        created_at: datetime,
        integrity_status: PatchIntegrityStatus,
    ) -> BridgePatchExport:
        created = tuple(item.path for item in review.changed_files if item.change_type == "created")
        modified = tuple(item.path for item in review.changed_files if item.change_type == "modified")
        deleted = tuple(item.path for item in review.changed_files if item.change_type == "deleted")
        return BridgePatchExport(
            review_id=review.review_id,
            patch_id=patch_id,
            provider_id=review.provider_id,
            patch_path=patch_id,
            patch_size=patch_size,
            patch_sha256=patch_sha256,
            file_count=len(review.changed_files),
            changed_file_count=len(review.changed_files),
            created_files=created,
            modified_files=modified,
            deleted_files=deleted,
            workspace_root_hash=review.workspace_root_hash or "",
            review_status_at_export=review.status,
            apply_enabled=False,
            integrity_status=integrity_status,
            created_at=created_at,
            download_url=f"/models/bridges/reviews/{review.review_id}/patch",
        )

    def patch_text(self, review_id: str) -> str:
        review = self._get_review(review_id)
        path = self._patch_path(review.review_id)
        if not path.exists():
            self.export_patch(review_id)
        metadata = self.verify_patch(review_id)
        self._record_audit(
            "bridge_patch_viewed",
            review,
            self.patch_size(review_id),
            patch_sha256=metadata.patch_sha256,
            integrity_status=metadata.integrity_status,
        )
        return self._patch_path(review.review_id).read_text(encoding="utf-8")

    def record_patch_copied(self, review_id: str) -> None:
        review = self._get_review(review_id)
        metadata = self.verify_patch(review_id)
        self._record_audit(
            "bridge_patch_copied",
            review,
            self.patch_size(review_id),
            patch_sha256=metadata.patch_sha256,
            integrity_status=metadata.integrity_status,
        )

    def patch_size(self, review_id: str) -> int:
        path = self._patch_path(review_id)
        return path.stat().st_size if path.exists() else 0

    def _patch_path(self, review_id: str) -> Path:
        path = (self.patch_directory / f"{review_id}.patch").resolve()
        try:
            path.relative_to(self.patch_directory.resolve())
        except ValueError as exc:
            raise BridgePatchExportError("Patch path escaped patch directory.", {"review_id": review_id}) from exc
        return path

    def _metadata_path(self, review_id: str) -> Path:
        path = (self.patch_directory / f"{review_id}.metadata.json").resolve()
        try:
            path.relative_to(self.patch_directory.resolve())
        except ValueError as exc:
            raise BridgePatchExportError("Patch metadata path escaped patch directory.", {"review_id": review_id}) from exc
        return path

    def _write_metadata(self, export: BridgePatchExport) -> None:
        self.patch_directory.mkdir(parents=True, exist_ok=True)
        self._metadata_path(export.review_id).write_text(
            json.dumps(export.to_dict(), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.patch_store.upsert(self._record_from_export(export))

    def _record_from_export(self, export: BridgePatchExport) -> BridgePatchRecord:
        return BridgePatchRecord(
            patch_id=export.patch_id,
            review_id=export.review_id,
            provider_id=export.provider_id,
            created_at=export.created_at,
            patch_path=export.patch_path,
            metadata_path=f"{export.review_id}.metadata.json",
            patch_size=export.patch_size,
            patch_sha256=export.patch_sha256,
            integrity_status=export.integrity_status,
            changed_file_count=export.changed_file_count,
            created_files=export.created_files,
            modified_files=export.modified_files,
            deleted_files=export.deleted_files,
            workspace_root_hash=export.workspace_root_hash,
            review_status_at_export=export.review_status_at_export,
            apply_enabled=export.apply_enabled,
        )

    def _get_review(self, review_id: str) -> BridgeReviewSession:
        try:
            return self.review_service.get_review(review_id)
        except BridgeDiffError as exc:
            raise BridgePatchExportError("Bridge review was not found.", {"review_id": review_id}) from exc

    def _build_patch(self, review: BridgeReviewSession) -> str:
        if not review.changed_files:
            raise BridgePatchExportError("Bridge review has no changed files to export.", {"review_id": review.review_id})
        parts = [
            "# ForgeX bridge review patch\n",
            f"# Review: {review.review_id}\n",
            f"# Provider: {review.provider_id}\n",
            "# This patch export does not apply changes to your workspace.\n\n",
        ]
        for changed_file in review.changed_files:
            if not is_safe_relative_path(changed_file.path):
                raise BridgePatchExportError("Unsafe review path cannot be exported.", {"path": changed_file.path})
            if not changed_file.preview_supported or not changed_file.diff_preview:
                parts.extend(
                    [
                        f"diff --git a/{changed_file.path} b/{changed_file.path}\n",
                        f"# Binary or unsupported diff preview for {changed_file.path}\n",
                        f"# {changed_file.warning or 'Diff preview is unavailable.'}\n\n",
                    ]
                )
                continue
            diff = normalize_patch_diff(changed_file.diff_preview, changed_file.path)
            parts.append(diff)
            if not diff.endswith("\n"):
                parts.append("\n")
            parts.append("\n")
        patch = "".join(parts)
        if len(patch) > self.max_patch_chars:
            patch = patch[: self.max_patch_chars].rstrip() + "\n# ... [patch truncated]\n"
        if review.workspace_root and review.workspace_root in patch:
            raise BridgePatchExportError("Patch export would include a raw workspace path.", {"review_id": review.review_id})
        return patch

    def _record_audit(
        self,
        event: str,
        review: BridgeReviewSession,
        patch_size: int,
        *,
        patch_sha256: str = "",
        integrity_status: PatchIntegrityStatus | str = "unknown",
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=review.provider_id,
                workspace_root_hash=review.workspace_root_hash or "",
                changed_file_count=len(review.changed_files),
                approved=False,
                review_id=review.review_id,
                metadata={
                    "patch_size": patch_size,
                    "patch_sha256": patch_sha256,
                    "integrity_status": integrity_status,
                },
            )
        )

    def _record_patch_audit(self, event: str, record: BridgePatchRecord) -> None:
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
                    "patch_size": record.patch_size,
                    "patch_sha256": record.patch_sha256,
                    "integrity_status": record.integrity_status,
                },
            )
        )

    def _record_history_viewed(
        self,
        *,
        provider_id: str | None,
        review_id: str | None,
        integrity_status: str | None,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event="bridge_patch_history_viewed",
                provider_id=provider_id or "all",
                workspace_root_hash="",
                changed_file_count=0,
                approved=False,
                review_id=review_id,
                metadata={"integrity_status": integrity_status},
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


def export_from_dict(data: dict[str, Any]) -> BridgePatchExport:
    return BridgePatchExport(
        review_id=str(data["review_id"]),
        patch_id=str(data["patch_id"]),
        provider_id=str(data["provider_id"]),
        patch_path=str(data["patch_path"]),
        patch_size=int(data["patch_size"]),
        patch_sha256=str(data.get("patch_sha256", "")),
        file_count=int(data.get("file_count", data.get("changed_file_count", 0))),
        changed_file_count=int(data.get("changed_file_count", data.get("file_count", 0))),
        created_files=tuple(str(item) for item in data.get("created_files", [])),
        modified_files=tuple(str(item) for item in data.get("modified_files", [])),
        deleted_files=tuple(str(item) for item in data.get("deleted_files", [])),
        workspace_root_hash=str(data.get("workspace_root_hash", "")),
        review_status_at_export=str(data.get("review_status_at_export", "unknown")),
        apply_enabled=bool(data.get("apply_enabled", False)),
        integrity_status=data.get("integrity_status", "unknown"),
        created_at=datetime.fromisoformat(str(data["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        download_url=str(data.get("download_url", f"/models/bridges/reviews/{data['review_id']}/patch")),
    )


def export_from_record(record: BridgePatchRecord) -> BridgePatchExport:
    return BridgePatchExport(
        review_id=record.review_id,
        patch_id=record.patch_id,
        provider_id=record.provider_id,
        patch_path=record.patch_path,
        patch_size=record.patch_size,
        patch_sha256=record.patch_sha256,
        file_count=record.changed_file_count,
        changed_file_count=record.changed_file_count,
        created_files=record.created_files,
        modified_files=record.modified_files,
        deleted_files=record.deleted_files,
        workspace_root_hash=record.workspace_root_hash,
        review_status_at_export=record.review_status_at_export,
        apply_enabled=record.apply_enabled,
        integrity_status=record.integrity_status,
        created_at=record.created_at,
        download_url=f"/models/bridges/reviews/{record.review_id}/patch",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    command = ["open", str(path)] if os.sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def normalize_patch_diff(diff_preview: str, path: str) -> str:
    sanitized = sanitize_diff_paths(diff_preview, path)
    if sanitized.startswith("diff --git "):
        return sanitized
    return f"diff --git a/{path} b/{path}\n{sanitized}"


def sanitize_diff_paths(diff_preview: str, path: str) -> str:
    lines = []
    for line in diff_preview.splitlines(keepends=True):
        if line.startswith("--- "):
            lines.append(f"--- a/{path}\n")
        elif line.startswith("+++ "):
            lines.append(f"+++ b/{path}\n")
        elif re.match(r"^diff --git ", line):
            lines.append(f"diff --git a/{path} b/{path}\n")
        else:
            lines.append(line)
    return "".join(lines)
