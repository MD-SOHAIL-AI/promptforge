"""Workspace snapshot and diff support for bridge review."""

from __future__ import annotations

import difflib
import hashlib
import mimetypes
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from .audit_log import BridgeAuditLog, hash_workspace_root
from .review_models import (
    BridgeApprovalDecision,
    BridgeAuditEntry,
    BridgeChangedFile,
    BridgeReviewSession,
    BridgeWorkspaceSnapshot,
    BridgeSnapshotFile,
)
from .review_store import BridgeReviewStore


IGNORED_DIRS = {".git", ".pio", ".promptforge", "node_modules", "dist", "build", ".next", ".forgex"}
IGNORED_INTERNAL_FILES = {".forgex-agy-trusted-workspace.json", ".forgex-agy-runtime-prompt"}
MAX_FILE_BYTES = 512_000
MAX_DIFF_CHARS = 12_000
REVIEW_TTL_MINUTES = 24 * 60


class BridgeDiffError(ValueError):
    code = "BRIDGE_DIFF_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class BridgeDiffService:
    def __init__(
        self,
        *,
        audit_log: BridgeAuditLog | None = None,
        store: BridgeReviewStore | None = None,
        max_file_bytes: int = MAX_FILE_BYTES,
        max_diff_chars: int = MAX_DIFF_CHARS,
    ) -> None:
        self.audit_log = audit_log
        self.store = store
        self.max_file_bytes = max_file_bytes
        self.max_diff_chars = max_diff_chars
        self._snapshots: dict[str, BridgeWorkspaceSnapshot] = store.load_snapshots() if store is not None else {}
        self._sessions: dict[str, BridgeReviewSession] = store.load_reviews() if store is not None else {}

    def snapshot_workspace(self, workspace_root: str | Path) -> BridgeWorkspaceSnapshot:
        snapshot = self._snapshot_workspace(workspace_root)
        self._snapshots[snapshot.snapshot_id] = snapshot
        if self.store is not None:
            self.store.save_snapshot(snapshot)
        self._record_snapshot_audit(snapshot)
        return snapshot

    def inspect_workspace(self, workspace_root: str | Path) -> BridgeWorkspaceSnapshot:
        """Capture an in-memory snapshot without persistence or audit side effects."""

        return self._snapshot_workspace(workspace_root)

    def _snapshot_workspace(self, workspace_root: str | Path) -> BridgeWorkspaceSnapshot:
        root = self._resolve_workspace_root(workspace_root)
        files: dict[str, BridgeSnapshotFile] = {}
        for file_path in self._iter_files(root):
            relative = self._relative_safe_path(root, file_path)
            stat = file_path.stat()
            files[relative] = BridgeSnapshotFile(
                path=relative,
                hash=hash_file(file_path),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                content=read_text_preview(file_path, self.max_file_bytes),
            )
        return BridgeWorkspaceSnapshot(
            str(root),
            files,
            workspace_root_hash=hash_workspace_root(str(root)),
        )

    def get_snapshot(self, snapshot_id: str) -> BridgeWorkspaceSnapshot:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            raise BridgeDiffError("Bridge snapshot was not found.", {"snapshot_id": snapshot_id}) from exc

    def diff_snapshot(
        self,
        snapshot: BridgeWorkspaceSnapshot,
        workspace_root: str | Path | None = None,
    ) -> tuple[BridgeChangedFile, ...]:
        if workspace_root is None and not snapshot.workspace_root:
            raise BridgeDiffError(
                "Persisted snapshot metadata requires a workspace root to generate a diff.",
                {"snapshot_id": snapshot.snapshot_id},
            )
        root = self._resolve_workspace_root(workspace_root or snapshot.workspace_root)
        expected_hash = snapshot.workspace_root_hash or hash_workspace_root(snapshot.workspace_root)
        if hash_workspace_root(str(root)) != expected_hash:
            raise BridgeDiffError("Snapshot workspace does not match requested workspace.", {"workspace_root": str(root)})
        current = self._snapshot_workspace(root)
        changed: list[BridgeChangedFile] = []
        all_paths = sorted(set(snapshot.files) | set(current.files))
        for relative_path in all_paths:
            safe = is_safe_relative_path(relative_path)
            if not safe:
                changed.append(
                    BridgeChangedFile(
                        path=relative_path,
                        change_type="modified",
                        safe=False,
                        warning="Unsafe path rejected.",
                    )
                )
                continue
            before = snapshot.files.get(relative_path)
            after = current.files.get(relative_path)
            if before is None and after is not None:
                diff_preview, supported, warning = self._diff_preview(relative_path, "", root / relative_path)
                changed.append(
                    BridgeChangedFile(
                        path=relative_path,
                        change_type="created",
                        safe=True,
                        previous_hash=None,
                        new_hash=after.hash,
                        diff_preview=diff_preview,
                        preview_supported=supported,
                        warning=warning,
                    )
                )
            elif before is not None and after is None:
                diff_preview, supported, warning = self._diff_preview(relative_path, before.content, None, before_hash=before.hash)
                changed.append(
                    BridgeChangedFile(
                        path=relative_path,
                        change_type="deleted",
                        safe=True,
                        previous_hash=before.hash,
                        new_hash=None,
                        diff_preview=diff_preview,
                        preview_supported=supported,
                        warning=warning,
                    )
                )
            elif before is not None and after is not None and before.hash != after.hash:
                diff_preview, supported, warning = self._diff_preview(relative_path, before.content, root / relative_path)
                changed.append(
                    BridgeChangedFile(
                        path=relative_path,
                        change_type="modified",
                        safe=True,
                        previous_hash=before.hash,
                        new_hash=after.hash,
                        diff_preview=diff_preview,
                        preview_supported=supported,
                        warning=warning,
                    )
                )
        return tuple(changed)

    def create_review(
        self,
        *,
        provider_id: str,
        workspace_root: str | Path,
        snapshot: BridgeWorkspaceSnapshot,
        artifact_source: str | None = None,
        artifact_type: str | None = None,
        artifact_metadata: dict[str, object] | None = None,
    ) -> BridgeReviewSession:
        root = self._resolve_workspace_root(workspace_root)
        expected_hash = snapshot.workspace_root_hash or hash_workspace_root(snapshot.workspace_root)
        if snapshot.workspace_root and Path(snapshot.workspace_root).resolve() != root:
            raise BridgeDiffError("Snapshot workspace does not match requested workspace.", {"workspace_root": str(root)})
        if hash_workspace_root(str(root)) != expected_hash:
            raise BridgeDiffError("Snapshot workspace does not match requested workspace.", {"workspace_root": str(root)})
        changed_files = self.diff_snapshot(snapshot, root)
        created_at = datetime.now(timezone.utc)
        review = BridgeReviewSession(
            review_id=f"bridge-review-{uuid.uuid4().hex}",
            provider_id=provider_id,
            workspace_root=str(root),
            workspace_root_hash=hash_workspace_root(str(root)),
            status="pending",
            created_at=created_at,
            expires_at=created_at + timedelta(minutes=REVIEW_TTL_MINUTES),
            changed_files=changed_files,
            summary=summarize_changes(changed_files),
            artifact_source=artifact_source,  # type: ignore[arg-type]
            artifact_type=artifact_type,  # type: ignore[arg-type]
            artifact_metadata=artifact_metadata,
        )
        self._sessions[review.review_id] = review
        if self.store is not None:
            self.store.save_review(review)
        self._record_audit("diff_generated", review, approved=False)
        self._record_audit(
            "review_created",
            review,
            approved=False,
        )
        return review

    def get_review(self, review_id: str) -> BridgeReviewSession:
        try:
            review = self._sessions[review_id]
        except KeyError as exc:
            raise BridgeDiffError("Bridge review was not found.", {"review_id": review_id}) from exc
        if review.status == "pending" and datetime.now(timezone.utc) > review.expires_at:
            review = self._replace_status(review, "expired")
            self._sessions[review.review_id] = review
            if self.store is not None:
                self.store.save_review(review)
            self._record_audit("review_expired", review, approved=False)
        return review

    def list_reviews(
        self,
        *,
        status: str | None = None,
        provider_id: str | None = None,
    ) -> tuple[BridgeReviewSession, ...]:
        reviews = [self.get_review(review_id) for review_id in list(self._sessions)]
        if status is not None:
            reviews = [review for review in reviews if review.status == status]
        if provider_id is not None:
            reviews = [review for review in reviews if review.provider_id == provider_id]
        return tuple(sorted(reviews, key=lambda item: item.created_at, reverse=True))

    def review_counts(self) -> dict[str, int]:
        counts = {"pending": 0, "approved": 0, "rejected": 0, "expired": 0}
        for review in self.list_reviews():
            counts[review.status] += 1
        return counts

    def cleanup_expired_reviews(self) -> dict[str, int]:
        before = len(self._sessions)
        for review_id in list(self._sessions):
            self.get_review(review_id)
        self._sessions = {
            review_id: review
            for review_id, review in self._sessions.items()
            if review.status != "expired"
        }
        if self.store is not None:
            self.store.replace_reviews(self._sessions)
        return {"removed": before - len(self._sessions), "remaining": len(self._sessions)}

    def approve_review(self, review_id: str) -> tuple[BridgeReviewSession, BridgeApprovalDecision]:
        review = self._transition_review(review_id, "approved")
        decision = BridgeApprovalDecision(review.review_id, "approved")
        self._record_audit("review_approved", review, approved=True)
        return review, decision

    def reject_review(self, review_id: str) -> tuple[BridgeReviewSession, BridgeApprovalDecision]:
        review = self._transition_review(review_id, "rejected")
        decision = BridgeApprovalDecision(review.review_id, "rejected")
        self._record_audit("review_rejected", review, approved=False)
        return review, decision

    def _transition_review(self, review_id: str, status: str) -> BridgeReviewSession:
        review = self.get_review(review_id)
        if review.status != "pending":
            raise BridgeDiffError("Bridge review is not pending.", {"review_id": review_id, "status": review.status})
        decision = status if status in {"approved", "rejected"} else None
        updated = self._replace_status(review, status, decision=decision)  # type: ignore[arg-type]
        self._sessions[review_id] = updated
        if self.store is not None:
            self.store.save_review(updated)
        return updated

    def _replace_status(
        self,
        review: BridgeReviewSession,
        status: str,
        *,
        decision: str | None = None,
    ) -> BridgeReviewSession:
        return BridgeReviewSession(
            review_id=review.review_id,
            provider_id=review.provider_id,
            workspace_root=review.workspace_root,
            workspace_root_hash=review.workspace_root_hash or hash_workspace_root(review.workspace_root),
            status=status,  # type: ignore[arg-type]
            created_at=review.created_at,
            expires_at=review.expires_at,
            changed_files=review.changed_files,
            summary=review.summary,
            decision=decision or review.decision,
            artifact_source=review.artifact_source,
            artifact_type=review.artifact_type,
            artifact_metadata=review.artifact_metadata,
        )

    def _record_snapshot_audit(self, snapshot: BridgeWorkspaceSnapshot) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event="snapshot_created",
                provider_id="bridge_review",
                workspace_root_hash=snapshot.workspace_root_hash or hash_workspace_root(snapshot.workspace_root),
                changed_file_count=len(snapshot.files),
                approved=False,
                review_id=snapshot.snapshot_id,
            )
        )

    def _record_audit(self, event: str, review: BridgeReviewSession, *, approved: bool) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=review.provider_id,
                workspace_root_hash=hash_workspace_root(review.workspace_root),
                changed_file_count=len(review.changed_files),
                approved=approved,
                review_id=review.review_id,
            )
        )

    def _resolve_workspace_root(self, workspace_root: str | Path) -> Path:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise BridgeDiffError("Workspace root must be an existing directory.", {"workspace_root": str(root)})
        return root

    def _iter_files(self, root: Path) -> list[Path]:
        result: list[Path] = []
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if name.casefold() not in IGNORED_DIRS
            ]
            current = Path(current_root)
            for filename in filenames:
                if filename.casefold() in IGNORED_INTERNAL_FILES:
                    continue
                file_path = (current / filename).resolve()
                try:
                    file_path.relative_to(root)
                except ValueError as exc:
                    raise BridgeDiffError("Snapshot attempted to leave workspace root.", {"path": str(file_path)}) from exc
                result.append(file_path)
        return result

    def _relative_safe_path(self, root: Path, file_path: Path) -> str:
        try:
            relative = file_path.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise BridgeDiffError("Path is outside workspace root.", {"path": str(file_path)}) from exc
        if not is_safe_relative_path(relative):
            raise BridgeDiffError("Unsafe path rejected.", {"path": relative})
        return relative

    def _diff_preview(
        self,
        relative_path: str,
        before_content: str | None,
        after_path: Path | None,
        *,
        before_hash: str | None = None,
    ) -> tuple[str | None, bool, str | None]:
        if before_content is None:
            return None, False, "Binary or large file preview is not supported."
        before_text = before_content
        after_text = "" if after_path is None or not after_path.exists() else read_text_preview(after_path, self.max_file_bytes)
        if before_text is None or after_text is None:
            return None, False, "Binary or large file preview is not supported."
        diff = "".join(
            difflib.unified_diff(
                before_text.splitlines(keepends=True),
                after_text.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
        )
        if before_content is not None and after_path is None and not diff and before_hash:
            diff = f"--- a/{relative_path}\n+++ /dev/null\n@@ deleted file @@\n"
        if len(diff) > self.max_diff_chars:
            return diff[: self.max_diff_chars].rstrip() + "\n... [diff truncated]\n", True, "Diff preview was truncated."
        return diff, True, None


def is_safe_relative_path(path: str) -> bool:
    value = path.replace("\\", "/")
    if not value or value.startswith("/") or value.startswith("../") or "/../" in value or value == "..":
        return False
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.is_absolute():
        return False
    if Path(value).is_absolute():
        return False
    return True


def contains_ignored_path_part(path: str) -> bool:
    return any(part.casefold() in IGNORED_DIRS for part in Path(path.replace("\\", "/")).parts)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text_preview(path: Path, max_file_bytes: int) -> str | None:
    if path.stat().st_size > max_file_bytes:
        return None
    mime, _ = mimetypes.guess_type(path.name)
    if mime and not (mime.startswith("text/") or mime in {"application/json", "application/xml"}):
        return None
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def summarize_changes(changed_files: tuple[BridgeChangedFile, ...]) -> str:
    counts = {"created": 0, "modified": 0, "deleted": 0}
    unsafe = 0
    for item in changed_files:
        counts[item.change_type] += 1
        if not item.safe:
            unsafe += 1
    parts = [
        f"{counts['created']} created",
        f"{counts['modified']} modified",
        f"{counts['deleted']} deleted",
    ]
    if unsafe:
        parts.append(f"{unsafe} unsafe")
    return ", ".join(parts)
