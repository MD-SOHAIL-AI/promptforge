"""Rebuildable, bounded workspace intelligence for multi-file ForgeX tasks."""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend.changes import IGNORED_DIRS
from backend.security import has_high_confidence_secret
from backend.security.workspace_io import checked_path, read_bounded
from .tool_policy import is_sensitive_path

_TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".ino", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".json", ".toml", ".ini", ".yaml", ".yml", ".md", ".txt", ".html", ".css",
}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_SYMBOL_RE = re.compile(r"\b(?:class|struct|enum|def|function|void|int|bool|float|double|char|auto|const)\s+([A-Za-z_][A-Za-z0-9_]*)")
_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)
_IMPORT_RE = re.compile(r"\b(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+)|require\(['\"]([^'\"]+)['\"]\))")
_CACHE_LOCK = threading.RLock()
_INDEX_CACHE: OrderedDict[tuple[str, int, str], tuple[str, dict[str, "IndexedFile"]]] = OrderedDict()


@dataclass(frozen=True, slots=True)
class IndexedFile:
    path: str
    size: int
    sha256: str
    language: str
    symbols: tuple[str, ...]
    dependencies: tuple[str, ...]
    tokens: frozenset[str]


@dataclass(frozen=True, slots=True)
class WorkspaceCandidate:
    path: str
    score: float
    reasons: tuple[str, ...]


class WorkspaceIndex:
    def __init__(self, root: str | Path, *, max_file_bytes: int = 256_000,
                 revision_hint: str | None = None) -> None:
        self.root = Path(root).resolve(strict=True)
        self.max_file_bytes = max(8_192, min(int(max_file_bytes), 2_000_000))
        self.revision_hint = revision_hint if isinstance(revision_hint, str) and revision_hint else None
        self._files: dict[str, IndexedFile] = {}
        self.revision = ""
        self._built = False

    def _ensure_built(self) -> None:
        if self._built:
            return
        key = (str(self.root), self.max_file_bytes, self.revision_hint) if self.revision_hint else None
        if key is not None:
            with _CACHE_LOCK:
                cached = _INDEX_CACHE.get(key)
                if cached is not None:
                    _INDEX_CACHE.move_to_end(key)
                    self.revision, files = cached
                    self._files = dict(files)
                    self._built = True
                    return
        self.rebuild()
        if key is not None:
            with _CACHE_LOCK:
                _INDEX_CACHE[key] = (self.revision, dict(self._files))
                _INDEX_CACHE.move_to_end(key)
                while len(_INDEX_CACHE) > 8:
                    _INDEX_CACHE.popitem(last=False)

    def rebuild(self) -> str:
        files: dict[str, IndexedFile] = {}
        manifest = hashlib.sha256()
        total_bytes = 0
        visited = 0
        expires = time.monotonic() + 10
        for current_root, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(current_root)
            try:
                checked_path(self.root, current)
            except (OSError, ValueError):
                dirnames[:] = []
                continue
            safe_dirs = []
            for name in sorted(dirnames):
                if name.casefold() in IGNORED_DIRS or is_sensitive_path((current / name).relative_to(self.root).as_posix()):
                    continue
                try:
                    checked_path(self.root, current / name)
                except (OSError, ValueError):
                    continue
                safe_dirs.append(name)
            dirnames[:] = safe_dirs
            visited += 1
            if visited > 10_000 or time.monotonic() >= expires:
                break
            for name in sorted(filenames):
                path = current / name
                relative = path.relative_to(self.root).as_posix()
                visited += 1
                if visited > 10_000 or time.monotonic() >= expires:
                    break
                if is_sensitive_path(relative):
                    continue
                try:
                    info = checked_path(self.root, path)
                    size = info.st_size
                    # Hints need no full hashing of binaries or oversized files.
                    if not path.is_file() or size > self.max_file_bytes or total_bytes + size > 64 * 1024 * 1024:
                        continue
                    data = read_bounded(self.root, path, self.max_file_bytes)
                    total_bytes += len(data)
                except (OSError, ValueError):
                    continue
                digest = hashlib.sha256(data).hexdigest()
                manifest.update(relative.encode("utf-8")); manifest.update(digest.encode("ascii"))
                suffix = path.suffix.casefold()
                text = ""
                if suffix in _TEXT_SUFFIXES and size <= self.max_file_bytes:
                    try:
                        text = data.decode("utf-8", errors="replace")
                    except OSError:
                        text = ""
                if has_high_confidence_secret(text):
                    continue
                symbols = tuple(dict.fromkeys(_SYMBOL_RE.findall(text)))[:200]
                dependencies: list[str] = list(_INCLUDE_RE.findall(text))
                for match in _IMPORT_RE.findall(text):
                    dependencies.extend(item for item in match if item)
                tokens = frozenset(token.casefold() for token in _TOKEN_RE.findall(text[:200_000]))
                files[relative] = IndexedFile(
                    relative, size, digest, _language_for(path), symbols, tuple(dict.fromkeys(dependencies))[:200], tokens
                )
        self._files = files
        self._built = True
        self.revision = manifest.hexdigest()
        return self.revision

    def query(self, intent: str, *, limit: int = 12) -> tuple[WorkspaceCandidate, ...]:
        self._ensure_built()
        terms = [item.casefold() for item in _TOKEN_RE.findall(intent)]
        results: list[WorkspaceCandidate] = []
        for item in self._files.values():
            score = 0.0; reasons: list[str] = []
            path_lower = item.path.casefold()
            stem_tokens = set(_TOKEN_RE.findall(path_lower))
            for term in terms:
                if term in path_lower:
                    score += 4.0; reasons.append(f"path:{term}")
                if term in item.tokens:
                    score += 1.0; reasons.append(f"content:{term}")
                if any(term in symbol.casefold() for symbol in item.symbols):
                    score += 3.0; reasons.append(f"symbol:{term}")
                if term in stem_tokens:
                    score += 2.0
            if item.path == "platformio.ini":
                score += 1.5
                reasons.append("embedded-config")
            if score > 0:
                results.append(WorkspaceCandidate(item.path, score, tuple(dict.fromkeys(reasons))[:8]))
        results.sort(key=lambda value: (-value.score, value.path))
        return tuple(results[: max(1, min(int(limit), 50))])

    def get(self, path: str) -> IndexedFile | None:
        self._ensure_built()
        return self._files.get(path)

    def files(self) -> tuple[IndexedFile, ...]:
        self._ensure_built()
        return tuple(self._files[key] for key in sorted(self._files))


def _language_for(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".h": "cpp-header", ".hpp": "cpp-header",
        ".ino": "arduino", ".py": "python", ".ts": "typescript", ".tsx": "typescript-react", ".js": "javascript",
        ".json": "json", ".ini": "ini", ".toml": "toml", ".md": "markdown",
    }.get(suffix, suffix.lstrip(".") or "text")
