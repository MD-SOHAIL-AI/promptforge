"""Safe incremental writes for chunked code generation."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..contracts.generated_project import GeneratedFile


@dataclass(frozen=True, slots=True)
class ChunkedFileWrite:
    path: str
    status: str
    created: bool
    updated: bool
    previous_hash: str | None
    new_hash: str
    bytes_written: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "created": self.created,
            "updated": self.updated,
            "previous_hash": self.previous_hash,
            "new_hash": self.new_hash,
            "bytes": self.bytes_written,
            "bytes_written": self.bytes_written,
        }


@dataclass(frozen=True, slots=True)
class _OriginalFile:
    existed: bool
    content: bytes | None


class WorkspaceGenerationTransaction:
    """Restore every workspace file touched by one generation attempt."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Workspace root is not available: {root}")
        self.root = root
        self._originals: dict[Path, _OriginalFile] = {}
        self._expected_hashes: dict[Path, str | None] = {}
        self._finished = False

    def remember(self, target: Path) -> None:
        if self._finished or target in self._originals:
            return
        if target.is_symlink():
            raise ValueError(f"Workspace file cannot be a symlink: {target.relative_to(self.root)}")
        original_bytes = target.read_bytes() if target.is_file() else None
        self._originals[target] = _OriginalFile(
            existed=original_bytes is not None,
            content=original_bytes,
        )
        self._expected_hashes[target] = hashlib.sha256(original_bytes).hexdigest() if original_bytes is not None else None

    def record_written(self, target: Path, digest: str) -> None:
        if self._finished or target not in self._originals:
            raise ValueError("GENERATION_TRANSACTION_NOT_TRACKED")
        self._expected_hashes[target] = digest

    def commit(self) -> None:
        self._originals.clear()
        self._expected_hashes.clear()
        self._finished = True

    def rollback(self) -> bool:
        if self._finished:
            return False
        changed = bool(self._originals)
        # Validate the whole rollback set before restoring any file. A user
        # edit after generation started is NEVER silently overwritten.
        for target, expected in self._expected_hashes.items():
            if target.is_symlink() or (_hash_file(target) if target.is_file() else None) != expected:
                raise ValueError("GENERATION_ROLLBACK_CONFLICT: workspace file changed after generation write")
        for target, original in reversed(tuple(self._originals.items())):
            if original.existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(target, original.content or b"")
            elif target.exists():
                target.unlink()
                _remove_empty_parents(target.parent, self.root)
        self._originals.clear()
        self._expected_hashes.clear()
        self._finished = True
        return changed


class ChunkedGenerationWriter:
    """Write generated files atomically inside a workspace root."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        transaction: WorkspaceGenerationTransaction | None = None,
    ) -> None:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Workspace root is not available: {root}")
        self.root = root
        self.transaction = transaction

    def write_file(self, generated_file: GeneratedFile) -> ChunkedFileWrite:
        target = self._contained_file(generated_file.path)
        if target.exists() and target.is_symlink():
            raise ValueError(f"Workspace file cannot be a symlink: {generated_file.path}")
        if self.transaction is not None:
            self.transaction.remember(target)

        previous_hash = _hash_file(target) if target.is_file() else None
        encoded = generated_file.content.encode("utf-8")
        new_hash = hashlib.sha256(encoded).hexdigest()
        created = previous_hash is None
        updated = previous_hash != new_hash

        target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(target, encoded)
        if self.transaction is not None:
            self.transaction.record_written(target, new_hash)

        return ChunkedFileWrite(
            path=_normalize_path(generated_file.path),
            status="written",
            created=created,
            updated=updated,
            previous_hash=previous_hash,
            new_hash=new_hash,
            bytes_written=len(encoded),
        )

    def _contained_file(self, relative_path: str) -> Path:
        normalized = _normalize_path(relative_path)
        target = (self.root / normalized).resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Generated file path escapes workspace: {relative_path}") from exc
        return target


def _normalize_path(path: str) -> str:
    file = GeneratedFile(path=path, content="x")
    return file.path


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(target: Path, content: bytes) -> None:
    tmp = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _remove_empty_parents(directory: Path, root: Path) -> None:
    current = directory
    while current != root:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
