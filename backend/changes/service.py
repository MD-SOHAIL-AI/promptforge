"""Safe staged ChangeSet creation, inspection, apply and discard."""

from __future__ import annotations

import difflib
import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable

from .models import ChangeSet, ChangedFile, SnapshotFile, WorkspaceSnapshot


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
        authorization_id: str | None = None,
        authorization_source: str = "explicit_review",
        risk_level: str = "unassessed",
        review_required: bool = True,
    ) -> ChangeSet:
        active = _resolve_root(workspace_root)
        stage = Path(stage_root).resolve(strict=True)
        self._assert_owned_stage(stage)
        if Path(baseline.workspace_root).resolve() != active:
            raise ChangeSetError("CHANGE_BASELINE_MISMATCH", "The baseline does not belong to the active workspace.")
        authorized = {
            _normalize_relative(path)
            for path in authorized_paths
            if path not in STAGE_ONLY_INTERNAL_FILES
        }
        staged_paths = {
            path.relative_to(stage).as_posix()
            for path in _iter_files(stage)
            if path.relative_to(stage).as_posix() not in STAGE_ONLY_INTERNAL_FILES
        }
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
        if not changed:
            raise ChangeSetError("CHANGE_NO_CHANGES", "The staged result does not change the workspace.")

        change_set = ChangeSet(
            change_set_id=f"changeset-{uuid.uuid4().hex}",
            provider_id=provider_id,
            workspace_root=str(active),
            staging_root=str(stage),
            status="pending",
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
            prepared: list[tuple[ChangedFile, Path, bytes, bytes | None]] = []
            for item in change_set.changed_files:
                target = _safe_join(active, item.path, must_exist=False)
                source = _safe_join(stage, item.path, must_exist=True)
                staged_bytes = source.read_bytes()
                if hashlib.sha256(staged_bytes).hexdigest() != item.new_hash:
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
            undo_root.mkdir(parents=False, exist_ok=True)
            applied: list[tuple[Path, bytes | None]] = []
            try:
                for item, target, staged_bytes, original in prepared:
                    if original is not None:
                        backup = undo_root.joinpath(*item.path.split("/"))
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write_bytes(backup, original)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _reject_symlink_components(active, target.parent)
                    _atomic_write_bytes(target, staged_bytes)
                    applied.append((target, original))
            except OSError as exc:
                # Best-effort transactional rollback for the bounded set of text files.
                for target, original in reversed(applied):
                    try:
                        if original is None:
                            target.unlink(missing_ok=True)
                        else:
                            _atomic_write_bytes(target, original)
                    except OSError:
                        pass
                self._mark(change_set, "failed")
                raise ChangeSetError("CHANGE_APPLY_FAILED", "The ChangeSet could not be applied safely.") from exc

            updated = change_set.with_status("applied", applied_at=datetime.now(timezone.utc))
            self._sets[change_set_id] = updated
            self._persist(updated)
            return updated

    def undo(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            change_set = self.get(change_set_id)
            if change_set.status != "applied":
                raise ChangeSetError("CHANGE_SET_NOT_APPLIED", "Only applied ChangeSets can be undone.")
            active = _resolve_root(change_set.workspace_root)
            stage = Path(change_set.staging_root).resolve(strict=True)
            self._assert_owned_stage(stage)
            undo_root = stage / "__undo__"

            # Never overwrite user edits that happened after the ChangeSet apply.
            for item in change_set.changed_files:
                target = _safe_join(active, item.path, must_exist=False)
                if not target.exists() or not target.is_file() or target.is_symlink():
                    raise ChangeSetError("CHANGE_UNDO_CONFLICT", "A changed file no longer matches the applied ChangeSet.", {"path": item.path})
                if item.new_hash is None or hash_file(target) != item.new_hash:
                    raise ChangeSetError("CHANGE_UNDO_CONFLICT", "A changed file was edited after the ChangeSet was applied.", {"path": item.path})

            restored: list[tuple[Path, bytes]] = []
            deleted_created: list[tuple[Path, bytes]] = []
            try:
                for item in reversed(change_set.changed_files):
                    target = _safe_join(active, item.path, must_exist=True)
                    current = target.read_bytes()
                    if item.change_type == "created":
                        target.unlink()
                        deleted_created.append((target, current))
                        continue
                    backup = _safe_join(undo_root, item.path, must_exist=True)
                    original = backup.read_bytes()
                    _atomic_write_bytes(target, original)
                    restored.append((target, current))
            except (OSError, ChangeSetError) as exc:
                # Best effort to restore the applied state if undo itself fails.
                for target, applied_bytes in reversed(restored):
                    try:
                        _atomic_write_bytes(target, applied_bytes)
                    except OSError:
                        pass
                for target, applied_bytes in reversed(deleted_created):
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        _atomic_write_bytes(target, applied_bytes)
                    except OSError:
                        pass
                if isinstance(exc, ChangeSetError):
                    raise
                raise ChangeSetError("CHANGE_UNDO_FAILED", "The ChangeSet could not be undone safely.") from exc

            updated = change_set.with_status("undone", undone_at=datetime.now(timezone.utc))
            self._sets[change_set_id] = updated
            self._persist(updated)
            shutil.rmtree(stage, ignore_errors=True)
            return updated

    def discard(self, change_set_id: str) -> ChangeSet:
        with self._lock:
            change_set = self.get(change_set_id)
            if change_set.status != "pending":
                raise ChangeSetError("CHANGE_SET_NOT_PENDING", "Only pending ChangeSets can be discarded.")
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
        self._sets[change_set.change_set_id] = updated
        self._persist(updated)

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
    result: list[Path] = []
    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        if ignore_common_dirs:
            dirnames[:] = [
                name for name in dirnames
                if name.casefold() not in IGNORED_DIRS and not (current / name).is_symlink()
            ]
        else:
            dirnames[:] = [name for name in dirnames if not (current / name).is_symlink()]
        for name in filenames:
            path = current / name
            if path.is_symlink() or not path.is_file():
                continue
            result.append(path)
    return result


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
        source = _safe_join(stage, item.path, must_exist=True)
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
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
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
