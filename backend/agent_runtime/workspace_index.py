"""Rebuildable, bounded workspace intelligence for multi-file ForgeX tasks."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend.changes import IGNORED_DIRS

_TEXT_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".ino", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".json", ".toml", ".ini", ".yaml", ".yml", ".md", ".txt", ".html", ".css",
}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_SYMBOL_RE = re.compile(r"\b(?:class|struct|enum|def|function|void|int|bool|float|double|char|auto|const)\s+([A-Za-z_][A-Za-z0-9_]*)")
_INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)
_IMPORT_RE = re.compile(r"\b(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+)|require\(['\"]([^'\"]+)['\"]\))")


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
    def __init__(self, root: str | Path, *, max_file_bytes: int = 256_000) -> None:
        self.root = Path(root).resolve(strict=True)
        self.max_file_bytes = max(8_192, min(int(max_file_bytes), 2_000_000))
        self._files: dict[str, IndexedFile] = {}
        self.revision = ""

    def rebuild(self) -> str:
        files: dict[str, IndexedFile] = {}
        manifest = hashlib.sha256()
        for current_root, dirnames, filenames in os.walk(self.root, followlinks=False):
            current = Path(current_root)
            dirnames[:] = [name for name in sorted(dirnames) if name.casefold() not in IGNORED_DIRS]
            for name in sorted(filenames):
                path = current / name
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(self.root).as_posix()
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                digest = _hash_file(path)
                manifest.update(relative.encode("utf-8")); manifest.update(digest.encode("ascii"))
                suffix = path.suffix.casefold()
                text = ""
                if suffix in _TEXT_SUFFIXES and size <= self.max_file_bytes:
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        text = ""
                symbols = tuple(dict.fromkeys(_SYMBOL_RE.findall(text)))[:200]
                dependencies: list[str] = list(_INCLUDE_RE.findall(text))
                for match in _IMPORT_RE.findall(text):
                    dependencies.extend(item for item in match if item)
                tokens = frozenset(token.casefold() for token in _TOKEN_RE.findall(text[:200_000]))
                files[relative] = IndexedFile(
                    relative, size, digest, _language_for(path), symbols, tuple(dict.fromkeys(dependencies))[:200], tokens
                )
        self._files = files
        self.revision = manifest.hexdigest()
        return self.revision

    def query(self, intent: str, *, limit: int = 12) -> tuple[WorkspaceCandidate, ...]:
        if not self._files:
            self.rebuild()
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
        if not self._files:
            self.rebuild()
        return self._files.get(path)

    def files(self) -> tuple[IndexedFile, ...]:
        if not self._files:
            self.rebuild()
        return tuple(self._files[key] for key in sorted(self._files))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _language_for(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".c": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".h": "cpp-header", ".hpp": "cpp-header",
        ".ino": "arduino", ".py": "python", ".ts": "typescript", ".tsx": "typescript-react", ".js": "javascript",
        ".json": "json", ".ini": "ini", ".toml": "toml", ".md": "markdown",
    }.get(suffix, suffix.lstrip(".") or "text")
