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
        self._finished = False

    def remember(self, target: Path) -> None:
        if self._finished or target in self._originals:
            return
        if target.is_symlink():
            raise ValueError(f"Workspace file cannot be a symlink: {target.relative_to(self.root)}")
        self._originals[target] = _OriginalFile(
            existed=target.is_file(),
            content=target.read_bytes() if target.is_file() else None,
        )

    def commit(self) -> None:
        self._originals.clear()
        self._finished = True

    def rollback(self) -> bool:
        if self._finished:
            return False
        changed = bool(self._originals)
        for target, original in reversed(tuple(self._originals.items())):
            if original.existed:
                target.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(target, original.content or b"")
            elif target.exists():
                target.unlink()
                _remove_empty_parents(target.parent, self.root)
        self._originals.clear()
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
