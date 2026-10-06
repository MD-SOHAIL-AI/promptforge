"""Safe staged ChangeSet creation, inspection, apply and discard."""

from __future__ import annotations

import difflib
import hashlib
import json
import mimetypes
import os
import shutil
import stat
import tempfile
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .models import ChangeSet, ChangedFile, SnapshotFile, WorkspaceSnapshot
from backend.security.workspace_io import iter_workspace_files


IGNORED_DIRS = frozenset({".git", ".pio", ".promptforge", ".forgex", ".next", "node_modules", "dist", "build"})
MAX_CONTEXT_FILE_BYTES = 512_000
MAX_DIFF_CHARS = 12_000
STAGE_ONLY_INTERNAL_FILES = frozenset({".promptforge-project.json"})


class ChangeSetError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(message)


class ChangeSetService:
    """Own staged writes independently of any model/provider integration."""

    def __init__(self, *, state_root: str | Path, staging_root: str | Path) -> None:
        self.state_root = Path(state_root).resolve()
        self.staging_root = Path(staging_root).resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._sets: dict[str, ChangeSet] = self._load()
        self.recovery_issues: dict[str, str] = {}
        for identifier in tuple(self._sets):
            if self._journal_path(identifier).exists():
                self._recover_publish(identifier)

    def create_stage(self, run_id: str | None = None) -> Path:
        identifier = run_id or f"stage-{uuid.uuid4().hex}"
        if not identifier.replace("-", "").replace("_", "").isalnum():
            raise ChangeSetError("CHANGE_STAGE_ID_INVALID", "The stage identifier is invalid.")
        target = (self.staging_root / identifier).resolve()
        if target.parent != self.staging_root:
            raise ChangeSetError("CHANGE_STAGE_PATH_INVALID", "The stage path is invalid.")
        target.mkdir(parents=False, exist_ok=False)
        return target

    def inspect_workspace(self, workspace_root: str | Path) -> WorkspaceSnapshot:
        root = _resolve_root(workspace_root)
        files: dict[str, SnapshotFile] = {}
        for path in _iter_files(root):
            relative = path.relative_to(root).as_posix()
            if not is_safe_relative_path(relative):
                continue
            stat = path.stat()
            files[relative] = SnapshotFile(
                path=relative,
                hash=hash_file(path),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                content=read_text_preview(path, MAX_CONTEXT_FILE_BYTES),
            )
        tree_hash = _snapshot_hash(files)
        return WorkspaceSnapshot(
            str(root),
            files,
            revision=f"workspace:{tree_hash[:24]}",
            tree_hash=tree_hash,
        )

    def create_from_stage(
        self,
        *,
        provider_id: str,
        workspace_root: str | Path,
        stage_root: str | Path,
        baseline: WorkspaceSnapshot,
        authorized_paths: Iterable[str],
        deleted_paths: Iterable[str] = (),
        authorization_id: str | None = None,
        authorization_source: str = "explicit_review",
        risk_level: str = "unassessed",
        review_required: bool = True,
        draft: bool = False,
    ) -> ChangeSet:
        active = _resolve_root(workspace_root)
        stage = Path(stage_root).resolve(strict=True)
        self._assert_owned_stage(stage)
        if Path(baseline.workspace_root).resolve() != active:
            raise ChangeSetError("CHANGE_BASELINE_MISMATCH", "The baseline does not belong to the active workspace.")
        staged_paths = {
            path.relative_to(stage).as_posix()
            for path in _iter_files(stage)
            if path.relative_to(stage).as_posix() not in STAGE_ONLY_INTERNAL_FILES
        }
        # File identity follows the host filesystem. On Windows an overlay
        # write can have different casing from its immutable baseline file.
        # Keep baseline spelling for review/undo while comparing identity keys.
        canonical_paths = {os.path.normcase(path): path for path in staged_paths}
        canonical_paths.update({os.path.normcase(path): path for path in baseline.files})
        def canonical(path: str) -> str:
            normalized = _normalize_relative(path)
            return canonical_paths.get(os.path.normcase(normalized), normalized)
        staged_paths = {canonical(path) for path in staged_paths}
        authorized = {canonical(path) for path in authorized_paths if path not in STAGE_ONLY_INTERNAL_FILES}
        deleted = {canonical(path) for path in deleted_paths}
        if deleted - authorized or deleted & staged_paths:
            raise ChangeSetError("CHANGE_DELETE_INVALID", "Deleted paths must be authorized and absent from the stage.")
        unexpected = sorted(staged_paths - authorized)
        if unexpected:
            raise ChangeSetError("CHANGE_EXTRA_FILES", "The stage contains files the model did not authorize.", {"paths": unexpected})

        changed: list[ChangedFile] = []
        for relative in sorted(staged_paths):
            if not is_safe_relative_path(relative):
                raise ChangeSetError("CHANGE_PATH_UNSAFE", "A staged path is unsafe.", {"path": relative})
            source = _safe_join(stage, relative, must_exist=True)
            previous = baseline.files.get(relative)
            new_hash = hash_file(source)
            if previous is not None and previous.hash == new_hash:
                continue
            change_type = "created" if previous is None else "modified"
            preview, supported, warning = _diff_preview(relative, previous.content if previous else "", source)
            changed.append(
                ChangedFile(
                    path=relative,
                    change_type=change_type,
                    previous_hash=previous.hash if previous else None,
                    new_hash=new_hash,
                    diff_preview=preview,
                    preview_supported=supported,
                    warning=warning,
                )
            )
        for relative in sorted(deleted):
            previous = baseline.files.get(relative)
            if previous is None:
                continue  # Creating then deleting a file is a net no-op.
            preview = None
            if previous.content is not None:
                preview = "\n".join(difflib.unified_diff(
                    previous.content.splitlines(), [], fromfile=relative,
                    tofile="/dev/null", lineterm="",
                ))[:MAX_DIFF_CHARS]
            changed.append(ChangedFile(
                path=relative, change_type="deleted", previous_hash=previous.hash,
                new_hash=None, diff_preview=preview,
                preview_supported=previous.content is not None,
            ))
        if not changed:
            raise ChangeSetError("CHANGE_NO_CHANGES", "The staged result does not change the workspace.")

        change_set = ChangeSet(
            change_set_id=f"changeset-{uuid.uuid4().hex}",
            provider_id=provider_id,
            workspace_root=str(active),
            staging_root=str(stage),
            status="draft" if draft else "pending",
            changed_files=tuple(changed),
            summary=_summarize(tuple(changed)),
            base_revision=baseline.revision,
            base_workspace_hash=baseline.tree_hash,
            staged_manifest_hash=_stage_manifest_hash(stage, tuple(changed)),
            authorization_id=authorization_id,
            authorization_source=authorization_source,
            risk_level=risk_level,
            review_required=review_required,
        )
        with self._lock:
            self._sets[change_set.change_set_id] = change_set
            self._persist(change_set)
        return change_set

    def create_review_draft(
        self, *, provider_id: str, workspace_root: str | Path,
        stage_root: str | Path, baseline: WorkspaceSnapshot,
    ) -> ChangeSet:
        """Retain bounded safe changes from a failed run for a future retry."""
        stage = Path(stage_root).resolve(strict=True)
        self._assert_owned_stage(stage)
        paths: list[str] = []
        total_bytes = 0
        for source in _iter_files(stage):
            relative = source.relative_to(stage).as_posix()
            if relative in STAGE_ONLY_INTERNAL_FILES:
                continue
            previous = baseline.files.get(relative)
            if previous is not None and previous.hash == hash_file(source):
                continue
            if not is_safe_relative_path(relative):
                raise ChangeSetError("CHANGE_PATH_FORBIDDEN", "A draft contains an unsafe changed path.")
            total_bytes += source.stat().st_size
            paths.append(relative)
            if len(paths) > 100 or total_bytes > 20_000_000:
                raise ChangeSetError("CHANGE_DRAFT_LIMIT", "The draft exceeds the recovery limit.")
        deleted = set(baseline.files) - {
            path.relative_to(stage).as_posix() for path in _iter_files(stage)
        }
        if len(paths) + len(deleted) > 100 or any(not is_safe_relative_path(path) for path in deleted):
            raise ChangeSetError("CHANGE_DRAFT_LIMIT", "The draft contains too many or unsafe deletions.")
        compact = self.create_stage()
        try:
            for relative in paths:
                target = compact.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage.joinpath(*relative.split("/")), target)
            draft = self.create_from_stage(
                provider_id=provider_id, workspace_root=workspace_root,
                stage_root=compact, baseline=baseline,
                authorized_paths=(*paths, *deleted), deleted_paths=deleted,
                authorization_source="recovery_draft", draft=True,
            )
            self.prune_review_drafts(Path(workspace_root).resolve(), keep=3)
            return draft
        except Exception:
            shutil.rmtree(compact, ignore_errors=True)
            raise

    def materialize_review_draft(
        self, change_set_id: str, *, workspace_root: str | Path, target_stage: str | Path,
    ) -> None:
        """Seed a fresh run only when both workspace and draft still match."""
        draft = self.get(change_set_id)
        if draft.status != "draft" or Path(draft.workspace_root).resolve() != Path(workspace_root).resolve():
            raise ChangeSetError("CHANGE_DRAFT_NOT_READY", "The recovery draft is unavailable.")
        if self.inspect_workspace(workspace_root).tree_hash != draft.base_workspace_hash:
            raise ChangeSetError("CHANGE_CONFLICT", "The workspace changed after the draft was saved.")
        source_root = Path(draft.staging_root).resolve(strict=True)
        self._assert_owned_stage(source_root)
        if _stage_manifest_hash(source_root, draft.changed_files) != draft.staged_manifest_hash:
            raise ChangeSetError("CHANGE_STAGE_TAMPERED", "The recovery draft changed after review.")
        target_root = Path(target_stage).resolve(strict=True)
        self._assert_owned_stage(target_root)
        for item in draft.changed_files:
            relative = _normalize_relative(item.path)
            target = target_root.joinpath(*relative.split("/"))
            if item.change_type == "deleted":
                target.unlink(missing_ok=True)
            else:
                source = _safe_join(source_root, relative, must_exist=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def prune_review_drafts(self, workspace_root: Path, *, keep: int = 3) -> None:
        # A ChangeSet is a durable reference, not an expendable cache. Only
        # explicit discard retires it; orphan stage retention handles data
        # with no ChangeSet/checkpoint reference. A count-based prune could
        # silently destroy the only draft belonging to a saved run.
        return

    def get(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            try:
                return self._sets[change_set_id]
            except KeyError as exc:
                raise ChangeSetError("CHANGE_SET_NOT_FOUND", "The requested ChangeSet was not found.") from exc

    def list(self, *, status: str | None = None) -> tuple[ChangeSet, ...]:
        with self._lock:
            values = tuple(self._sets.values())
        if status:
            values = tuple(item for item in values if item.status == status)
        return tuple(sorted(values, key=lambda item: item.created_at, reverse=True))

    def annotate_authorization(
        self,
        change_set_id: str,
        *,
        authorization_id: str | None,
        authorization_source: str,
        risk_level: str,
        review_required: bool,
    ) -> ChangeSet:
        with self._lock:
            current = self.get(change_set_id)
            if current.status != "pending":
                raise ChangeSetError("CHANGE_SET_NOT_PENDING", "Only pending ChangeSets can be annotated.")
            updated = replace(
                current,
                authorization_id=authorization_id,
                authorization_source=authorization_source,
                risk_level=risk_level,
                review_required=bool(review_required),
            )
            self._sets[change_set_id] = updated
            self._persist(updated)
            return updated

    def apply(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            change_set = self.get(change_set_id)
            if self._journal_path(change_set_id).exists():
                self._recover_publish(change_set_id)
                change_set = self.get(change_set_id)
            if change_set.status != "pending":
                raise ChangeSetError("CHANGE_SET_NOT_PENDING", "Only pending ChangeSets can be applied.")
            active = _resolve_root(change_set.workspace_root)
            stage = Path(change_set.staging_root).resolve(strict=True)
            self._assert_owned_stage(stage)

            current_snapshot = self.inspect_workspace(active)
            if (
                change_set.base_workspace_hash
                and current_snapshot.tree_hash != change_set.base_workspace_hash
            ):
                self._mark(change_set, "conflicted")
                raise ChangeSetError(
                    "CHANGE_CONFLICT",
                    "The workspace changed after this ChangeSet was prepared.",
                    {
                        "conflict_type": "workspace_revision",
                        "expected_revision": change_set.base_revision,
                        "actual_revision": current_snapshot.revision,
                    },
                )
            if (
                change_set.staged_manifest_hash
                and _stage_manifest_hash(stage, change_set.changed_files)
                != change_set.staged_manifest_hash
            ):
                self._mark(change_set, "failed")
                raise ChangeSetError(
                    "CHANGE_STAGE_TAMPERED",
                    "The staged ChangeSet manifest changed after review.",
                )

            # Validate every conflict before writing any target.
            prepared: list[tuple[ChangedFile, Path, bytes | None, bytes | None]] = []
            for item in change_set.changed_files:
                if item.path.split("/", 1)[0].rstrip(" .").casefold() == "__undo__":
                    raise ChangeSetError("CHANGE_UNDO_BACKUP_INVALID", "The undo backup namespace cannot be changed.")
                target = _safe_join(active, item.path, must_exist=False)
                source = _safe_join(stage, item.path, must_exist=item.change_type != "deleted")
                staged_bytes = None if item.change_type == "deleted" else source.read_bytes()
                if (item.change_type == "deleted" and (source.exists() or item.new_hash is not None)) or (
                    staged_bytes is not None and hashlib.sha256(staged_bytes).hexdigest() != item.new_hash
                ):
                    self._mark(change_set, "failed")
                    raise ChangeSetError("CHANGE_STAGE_TAMPERED", "A staged file changed after review.", {"path": item.path})
                current_bytes: bytes | None = None
                if target.exists():
                    if target.is_symlink() or not target.is_file():
                        self._mark(change_set, "conflicted")
                        raise ChangeSetError("CHANGE_TARGET_UNSAFE", "A target path is not a regular file.", {"path": item.path})
                    current_bytes = target.read_bytes()
                    current_hash = hashlib.sha256(current_bytes).hexdigest()
                    if item.previous_hash is None or current_hash != item.previous_hash:
                        self._mark(change_set, "conflicted")
                        raise ChangeSetError("CHANGE_CONFLICT", "The workspace changed after the ChangeSet was created.", {"path": item.path})
                elif item.previous_hash is not None:
                    self._mark(change_set, "conflicted")
                    raise ChangeSetError("CHANGE_CONFLICT", "A file expected by the ChangeSet no longer exists.", {"path": item.path})
                prepared.append((item, target, staged_bytes, current_bytes))

            undo_root = stage / "__undo__"
            applied: list[tuple[Path, bytes | None]] = []
            try:
                _reject_reparse_path(stage, undo_root)
                # A pending ChangeSet never owns a pre-existing backup directory.
                undo_root.mkdir(parents=False, exist_ok=False)
                for item, target, staged_bytes, original in prepared:
                    if original is not None:
                        backup = _backup_path(stage, item.path, must_exist=False)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        backup = _backup_path(stage, item.path, must_exist=False)
                        _atomic_write_bytes(backup, original)
                # Write-ahead: every original is durable before any active file
                # changes. Startup can reconcile even death between rename and
                # metadata publication, without trusting an in-memory cursor.
                self._begin_publish(change_set, "apply")
                for item, target, staged_bytes, original in prepared:
                    _reject_reparse_path(active, target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _reject_symlink_components(active, target.parent)
                    if staged_bytes is None:
                        target.unlink()
                        _sync_directory(target.parent)
                    else:
                        _atomic_write_bytes(target, staged_bytes)
                    applied.append((target, original))
            except (OSError, ChangeSetError) as exc:
                # Best-effort transactional rollback for the bounded set of text files.
                for target, original in reversed(applied):
                    try:
                        _reject_reparse_path(active, target)
                        if original is None:
                            target.unlink(missing_ok=True)
                        else:
                            _atomic_write_bytes(target, original)
                    except (OSError, ChangeSetError):
                        pass
                self._mark(change_set, "failed")
                if self._journal_path(change_set_id).exists():
                    self._recover_publish(change_set_id, failure_status="failed")
                raise ChangeSetError("CHANGE_APPLY_FAILED", "The ChangeSet could not be applied safely.") from exc

            updated = change_set.with_status("applied", applied_at=datetime.now(timezone.utc))
            self._persist(updated)
            self._sets[change_set_id] = updated
            self._finish_publish(change_set_id)
            return updated

    def undo(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            change_set = self.get(change_set_id)
            if self._journal_path(change_set_id).exists():
                self._recover_publish(change_set_id)
                change_set = self.get(change_set_id)
            if change_set.status != "applied":
                raise ChangeSetError("CHANGE_SET_NOT_APPLIED", "Only applied ChangeSets can be undone.")
            active = _resolve_root(change_set.workspace_root)
            stage = Path(change_set.staging_root).resolve(strict=True)
            self._assert_owned_stage(stage)
            undo_root = stage / "__undo__"

            # Validate and retain all backup bytes before mutating any target.
            originals: dict[str, bytes] = {}
            for item in change_set.changed_files:
                if item.change_type == "created":
                    continue
                try:
                    backup = _backup_path(stage, item.path, must_exist=True)
                    original = backup.read_bytes()
                except (OSError, ChangeSetError) as exc:
                    raise ChangeSetError("CHANGE_UNDO_BACKUP_INVALID", "An undo backup is missing or unsafe.", {"path": item.path}) from exc
                if item.previous_hash is None or hashlib.sha256(original).hexdigest() != item.previous_hash:
                    raise ChangeSetError("CHANGE_UNDO_BACKUP_INVALID", "An undo backup failed its integrity check.", {"path": item.path})
                originals[item.path] = original

            # Never overwrite user edits that happened after the ChangeSet apply.
            for item in change_set.changed_files:
                target = _safe_join(active, item.path, must_exist=False)
                _reject_reparse_path(active, target)
                if item.change_type == "deleted":
                    if target.exists():
                        raise ChangeSetError("CHANGE_UNDO_CONFLICT", "A deleted path has been recreated.", {"path": item.path})
                    continue
                if not target.exists() or not target.is_file() or target.is_symlink():
                    raise ChangeSetError("CHANGE_UNDO_CONFLICT", "A changed file no longer matches the applied ChangeSet.", {"path": item.path})
                if item.new_hash is None or hash_file(target) != item.new_hash:
                    raise ChangeSetError("CHANGE_UNDO_CONFLICT", "A changed file was edited after the ChangeSet was applied.", {"path": item.path})

            restored: list[tuple[Path, bytes | None]] = []
            deleted_created: list[tuple[Path, bytes]] = []
            try:
                self._begin_publish(change_set, "undo")
                for item in reversed(change_set.changed_files):
                    _reject_reparse_path(active, active.joinpath(*item.path.split("/")))
                    target = _safe_join(active, item.path, must_exist=item.change_type != "deleted")
                    current = None if item.change_type == "deleted" else target.read_bytes()
                    if item.change_type == "created":
                        target.unlink()
                        _sync_directory(target.parent)
                        deleted_created.append((target, current))
                        continue
                    original = originals[item.path]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _reject_symlink_components(active, target.parent)
                    _atomic_write_bytes(target, original)
                    restored.append((target, current))
            except (OSError, ChangeSetError) as exc:
                # Best effort to restore the applied state if undo itself fails.
                for target, applied_bytes in reversed(restored):
                    try:
                        _reject_reparse_path(active, target)
                        if applied_bytes is None:
                            target.unlink(missing_ok=True)
                        else:
                            _atomic_write_bytes(target, applied_bytes)
                    except (OSError, ChangeSetError):
                        pass
                for target, applied_bytes in reversed(deleted_created):
                    try:
                        _reject_reparse_path(active, target)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write_bytes(target, applied_bytes)
                    except (OSError, ChangeSetError):
                        pass
                self._recover_publish(change_set_id)
                if isinstance(exc, ChangeSetError):
                    raise
                raise ChangeSetError("CHANGE_UNDO_FAILED", "The ChangeSet could not be undone safely.") from exc

            updated = change_set.with_status("undone", undone_at=datetime.now(timezone.utc))
            self._persist(updated)
            self._sets[change_set_id] = updated
            self._finish_publish(change_set_id)
            shutil.rmtree(stage, ignore_errors=True)
            return updated

    def _journal_path(self, identifier: str) -> Path:
        return self.state_root / f".publish-{identifier}.json"

    def _begin_publish(self, change_set: ChangeSet, operation: str) -> None:
        path = self._journal_path(change_set.change_set_id)
        if path.exists():
            raise ChangeSetError("CHANGE_RECOVERY_REQUIRED", "An interrupted publication requires recovery.")
        _atomic_write_bytes(path, json.dumps({"schema_version": 1,
            "change_set_id": change_set.change_set_id, "operation": operation,
            "staged_manifest_hash": change_set.staged_manifest_hash}, sort_keys=True).encode())

    def _finish_publish(self, identifier: str) -> None:
        self._journal_path(identifier).unlink(missing_ok=True)
        _sync_directory(self.state_root)
        self.recovery_issues.pop(identifier, None)

    def _recover_publish(self, identifier: str, *, failure_status: str | None = None) -> None:
        """Idempotently roll back an interrupted apply/undo, preserving new edits."""
        change_set = self._sets[identifier]
        try:
            journal_path = self._journal_path(identifier)
            _reject_reparse_path(self.state_root, journal_path)
            if journal_path.stat().st_size > 4096:
                raise ValueError("invalid publication journal")
            journal = json.loads(journal_path.read_bytes())
            operation = journal["operation"]
            if (journal.get("schema_version") != 1 or journal.get("change_set_id") != identifier
                or operation not in {"apply", "undo"}
                or journal.get("staged_manifest_hash") != change_set.staged_manifest_hash):
                raise ValueError("invalid publication journal")
            committed = "applied" if operation == "apply" else "undone"
            if change_set.status == committed:
                self._finish_publish(identifier)
                return
            active = _resolve_root(change_set.workspace_root)
            stage = Path(change_set.staging_root).resolve(strict=True)
            self._assert_owned_stage(stage)
            prepared = []
            for item in change_set.changed_files:
                desired_hash = item.previous_hash if operation == "apply" else item.new_hash
                opposite_hash = item.new_hash if operation == "apply" else item.previous_hash
                target = _safe_join(active, item.path, must_exist=False)
                _reject_reparse_path(active, target)
                actual = hash_file(target) if target.exists() else None
                if actual not in {desired_hash, opposite_hash}:
                    raise ChangeSetError("CHANGE_RECOVERY_CONFLICT", "A file changed after interrupted publication.", {"path": item.path})
                data = None
                if desired_hash is not None and actual != desired_hash:
                    source = (_backup_path(stage, item.path, must_exist=True) if operation == "apply"
                              else _safe_join(stage, item.path, must_exist=True))
                    data = source.read_bytes()
                    if hashlib.sha256(data).hexdigest() != desired_hash:
                        raise ChangeSetError("CHANGE_RECOVERY_BACKUP_INVALID", "Recovery content failed its integrity check.")
                prepared.append((target, data, actual, desired_hash))
            for target, data, actual, desired in prepared:
                if actual == desired:
                    continue
                _reject_reparse_path(active, target)
                if data is None:
                    target.unlink(missing_ok=True)
                    _sync_directory(target.parent)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _reject_reparse_path(active, target)
                    _atomic_write_bytes(target, data)
            self._mark(change_set, failure_status or ("pending" if operation == "apply" else "applied"))
            if operation == "apply":
                # Pending retries must create their own backups afresh.
                undo_root = stage / "__undo__"
                _reject_reparse_path(stage, undo_root)
                if undo_root.exists():
                    shutil.rmtree(undo_root)
            self._finish_publish(identifier)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.recovery_issues[identifier] = getattr(exc, "code", "CHANGE_RECOVERY_FAILED")
            self._mark(change_set, failure_status or "conflicted")

    def discard(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            change_set = self.get(change_set_id)
            if change_set.status not in {"pending", "draft"}:
                raise ChangeSetError("CHANGE_SET_NOT_PENDING", "Only pending ChangeSets or drafts can be discarded.")
            stage = Path(change_set.staging_root)
            if stage.exists():
                try:
                    self._assert_owned_stage(stage.resolve())
                    shutil.rmtree(stage)
                except OSError as exc:
                    raise ChangeSetError("CHANGE_DISCARD_FAILED", "The staged files could not be discarded.") from exc
            updated = change_set.with_status("discarded")
            self._sets[change_set_id] = updated
            self._persist(updated)
            return updated

    def _mark(self, change_set: ChangeSet, status: str) -> None:
        updated = change_set.with_status(status)  # type: ignore[arg-type]
        self._persist(updated)
        self._sets[change_set.change_set_id] = updated

    def _assert_owned_stage(self, stage: Path) -> None:
        try:
            stage.relative_to(self.staging_root)
        except ValueError as exc:
            raise ChangeSetError("CHANGE_STAGE_OUTSIDE_ROOT", "The stage is outside the ForgeX staging root.") from exc
        if stage == self.staging_root:
            raise ChangeSetError("CHANGE_STAGE_ROOT_FORBIDDEN", "The staging root itself cannot be used as a ChangeSet.")

    def _metadata_path(self, change_set_id: str) -> Path:
        return self.state_root / f"{change_set_id}.json"

    def _persist(self, change_set: ChangeSet) -> None:
        payload = {
            **change_set.to_dict(),
            "staging_root": change_set.staging_root,
        }
        path = self._metadata_path(change_set.change_set_id)
        _atomic_write_bytes(path, (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8"))

    def _load(self) -> dict[str, ChangeSet]:
        result: dict[str, ChangeSet] = {}
        for path in self.state_root.glob("changeset-*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                changed = tuple(
                    ChangedFile(
                        path=str(item["path"]),
                        change_type=str(item["change_type"]),  # type: ignore[arg-type]
                        previous_hash=item.get("previous_hash"),
                        new_hash=item.get("new_hash"),
                        diff_preview=item.get("diff_preview"),
                        preview_supported=bool(item.get("preview_supported", True)),
                        warning=item.get("warning"),
                    )
                    for item in raw.get("changed_files", [])
                    if isinstance(item, dict)
                )
                created = datetime.fromisoformat(str(raw["created_at"]).replace("Z", "+00:00"))
                applied_raw = raw.get("applied_at")
                applied = datetime.fromisoformat(str(applied_raw).replace("Z", "+00:00")) if applied_raw else None
                undone_raw = raw.get("undone_at")
                undone = datetime.fromisoformat(str(undone_raw).replace("Z", "+00:00")) if undone_raw else None
                item = ChangeSet(
                    change_set_id=str(raw["change_set_id"]),
                    provider_id=str(raw["provider_id"]),
                    workspace_root=str(raw["workspace_root"]),
                    staging_root=str(raw["staging_root"]),
                    status=str(raw["status"]),  # type: ignore[arg-type]
                    changed_files=changed,
                    summary=str(raw.get("summary", "")),
                    base_revision=str(raw.get("base_revision", "")),
                    base_workspace_hash=str(raw.get("base_workspace_hash", "")),
                    staged_manifest_hash=str(raw.get("staged_manifest_hash", "")),
                    authorization_id=(str(raw["authorization_id"]) if raw.get("authorization_id") else None),
                    authorization_source=str(raw.get("authorization_source", "explicit_review")),
                    risk_level=str(raw.get("risk_level", "unassessed")),
                    review_required=bool(raw.get("review_required", True)),
                    created_at=created,
                    applied_at=applied,
                    undone_at=undone,
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            result[item.change_set_id] = item
        return result


def is_safe_relative_path(path: str) -> bool:
    try:
        _normalize_relative(path)
    except ChangeSetError:
        return False
    return True


def _normalize_relative(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ChangeSetError("CHANGE_PATH_INVALID", "Change paths must be non-empty strings.")
    # Use one canonical serialized path format on every host. Do not silently
    # reinterpret Windows separators after validation.
    if "\\" in path:
        raise ChangeSetError("CHANGE_PATH_UNSAFE", "Backslash paths are not allowed.", {"path": path})
    value = path.strip()
    if value in {".", ".."} or value.startswith("/"):
        raise ChangeSetError("CHANGE_PATH_UNSAFE", "The ChangeSet contains an unsafe relative path.", {"path": path})
    windows = PureWindowsPath(value)
    if windows.drive or windows.is_absolute() or Path(value).is_absolute():
        raise ChangeSetError("CHANGE_PATH_UNSAFE", "The ChangeSet contains an absolute path.", {"path": path})
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ChangeSetError("CHANGE_PATH_UNSAFE", "Path traversal is not allowed.", {"path": path})
    if any(part.startswith(".") for part in parts):
        raise ChangeSetError("CHANGE_PATH_FORBIDDEN", "Hidden/internal paths cannot be changed.", {"path": path})
    if any(part.casefold() in IGNORED_DIRS for part in parts):
        raise ChangeSetError("CHANGE_PATH_FORBIDDEN", "The ChangeSet targets an internal/generated directory.", {"path": path})
    lowered = [part.casefold() for part in parts]
    sensitive_names = {
        ".env", ".env.local", ".npmrc", ".pypirc", "credentials", "credentials.json",
        "secrets.json", "id_rsa", "id_ed25519", "known_hosts",
    }
    sensitive_markers = ("credential", "secret", "token", "private_key", "apikey", "api_key")
    if any(part in sensitive_names or any(marker in part for marker in sensitive_markers) for part in lowered):
        raise ChangeSetError("CHANGE_PATH_FORBIDDEN", "Sensitive credential paths cannot be changed.", {"path": path})
    return "/".join(parts)


def _resolve_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ChangeSetError("CHANGE_WORKSPACE_INVALID", "The workspace root must be a real directory.")
    return root


def _iter_files(root: Path, *, ignore_common_dirs: bool = True) -> list[Path]:
    try:
        return list(iter_workspace_files(root, ignored_dirs=IGNORED_DIRS if ignore_common_dirs else ()))
    except ValueError as exc:
        raise ChangeSetError("CHANGE_WORKSPACE_ENTRY_LIMIT", "The workspace exceeds the safe file inventory limit.") from exc


def _safe_join(root: Path, relative: str, *, must_exist: bool) -> Path:
    normalized = _normalize_relative(relative)
    target = root.joinpath(*normalized.split("/"))
    resolved_parent = target.parent.resolve(strict=True) if target.parent.exists() else _nearest_existing_parent(target.parent).resolve(strict=True)
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise ChangeSetError("CHANGE_PATH_ESCAPE", "A path escapes the allowed root.", {"path": relative}) from exc
    _reject_symlink_components(root, resolved_parent)
    if must_exist:
        resolved = target.resolve(strict=True)
        if resolved.is_symlink() or not resolved.is_file():
            raise ChangeSetError("CHANGE_PATH_INVALID", "A staged file is not a regular file.", {"path": relative})
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ChangeSetError("CHANGE_PATH_ESCAPE", "A path escapes the allowed root.", {"path": relative}) from exc
        return resolved
    if target.exists() and target.is_symlink():
        raise ChangeSetError("CHANGE_PATH_SYMLINK", "Symlink targets are not allowed.", {"path": relative})
    return target


def _reject_reparse_path(root: Path, target: Path) -> None:
    """Check lexical components before resolving away a link or Windows junction."""
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ChangeSetError("CHANGE_PATH_ESCAPE", "A path escapes the allowed root.") from exc
    current = root
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ChangeSetError("CHANGE_PATH_SYMLINK", "Links and reparse points are not allowed.")


def _backup_path(stage: Path, relative: str, *, must_exist: bool) -> Path:
    normalized = _normalize_relative(relative)
    target = stage / "__undo__" / normalized
    _reject_reparse_path(stage, target)
    return _safe_join(stage, "__undo__/" + normalized, must_exist=must_exist)


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _reject_symlink_components(root: Path, path: Path) -> None:
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ChangeSetError("CHANGE_PATH_ESCAPE", "A path escapes the workspace.") from exc
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ChangeSetError("CHANGE_PATH_SYMLINK", "Symlink path components are not allowed.")


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_hash(files: dict[str, SnapshotFile]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        item = files[relative]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.hash.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(item.size).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _stage_manifest_hash(stage: Path, changed_files: Iterable[ChangedFile]) -> str:
    digest = hashlib.sha256()
    for item in sorted(changed_files, key=lambda value: value.path):
        source = _safe_join(stage, item.path, must_exist=item.change_type != "deleted")
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        if item.change_type == "deleted" and not source.exists():
            digest.update(b"<deleted>")
        else:
            digest.update(hash_file(source).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def read_text_preview(path: Path, max_bytes: int) -> str | None:
    if path.stat().st_size > max_bytes:
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


def _diff_preview(relative: str, before: str | None, after_path: Path) -> tuple[str | None, bool, str | None]:
    after = read_text_preview(after_path, MAX_CONTEXT_FILE_BYTES)
    if before is None or after is None:
        return None, False, "Binary or large-file preview is unavailable."
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS].rstrip() + "\n... [diff truncated]\n", True, "Diff preview was truncated."
    return diff, True, None


def _summarize(changed: tuple[ChangedFile, ...]) -> str:
    created = sum(item.change_type == "created" for item in changed)
    modified = sum(item.change_type == "modified" for item in changed)
    deleted = sum(item.change_type == "deleted" for item in changed)
    return f"{created} created, {modified} modified, {deleted} deleted"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary: Path | None = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _sync_directory(path: Path) -> None:
    # Windows has no portable directory fsync; file handles are flushed before
    # replacement. POSIX also requires the directory entry to be durable.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
