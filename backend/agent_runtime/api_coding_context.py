"""Bounded workspace context builder for future API coding providers.

This module is read-only. It does not call providers, mutate workspaces, or
persist raw source bodies into workflow state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable


DEFAULT_MAX_FILES = 20
DEFAULT_MAX_FILE_BYTES = 32 * 1024
DEFAULT_MAX_TOTAL_BYTES = 128 * 1024
DEFAULT_TREE_ENTRY_LIMIT = 200
PROMPT_PREVIEW_CHARS = 240

SUPPORTED_CONTEXT_MODES = frozenset({"selected_files", "project_summary"})

GENERATED_DIRS = frozenset({
    ".git",
    "node_modules",
    "dist",
    "build",
    ".pio",
    "__pycache__",
    ".venv",
    "venv",
    "coverage",
    ".cache",
    ".pytest_cache",
    ".next",
})
SENSITIVE_EXACT_NAMES = frozenset({
    ".env",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
    ".netrc",
})
SENSITIVE_PREFIXES = ("secrets.", "credentials.", ".env.")
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
KEY_CONFIG_FILES = ("platformio.ini", "package.json", "pyproject.toml", "requirements.txt", "README.md")
DEFAULT_CONTEXT_ROOTS = ("src", "include", "lib")
TEXT_SUFFIXES = frozenset({
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".ino",
    ".ini",
    ".toml",
    ".json",
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".yaml",
    ".yml",
    ".csv",
})
SECRET_PATTERNS = (
    re.compile(r"(?im)^\s*OPENAI_API_KEY\s*="),
    re.compile(r"(?i)Authorization\s*:\s*Bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?im)^\s*api[_-]?key\s*[:=]"),
    re.compile(r"(?im)^\s*token\s*[:=]"),
    re.compile(r"(?im)^\s*password\s*[:=]"),
    re.compile(r"BEGIN\s+(?:RSA\s+|EC\s+|OPENSSH\s+)?PRIVATE KEY", re.IGNORECASE),
)
_BINARY_CONTROL_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True, slots=True)
class ApiCodingContextFile:
    path: str
    kind: str
    content: str = field(repr=False)
    size_bytes: int
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "content": self.content,
            "size_bytes": self.size_bytes,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ApiCodingContextExcludedFile:
    path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ApiCodingContextLimits:
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_tree_entries: int = DEFAULT_TREE_ENTRY_LIMIT

    def to_dict(self) -> dict[str, int]:
        return {
            "max_files": self.max_files,
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_tree_entries": self.max_tree_entries,
        }


@dataclass(frozen=True, slots=True)
class ApiCodingContext:
    workspace_label: str
    context_mode: str
    prompt_preview: str
    files: tuple[ApiCodingContextFile, ...]
    tree_summary: tuple[str, ...]
    excluded_files: tuple[ApiCodingContextExcludedFile, ...]
    limits: ApiCodingContextLimits
    total_bytes: int
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_label": self.workspace_label,
            "context_mode": self.context_mode,
            "prompt_preview": self.prompt_preview,
            "files": [item.to_dict() for item in self.files],
            "tree_summary": list(self.tree_summary),
            "excluded_files": [item.to_dict() for item in self.excluded_files],
            "limits": self.limits.to_dict(),
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ApiCodingContextListedFile:
    path: str
    kind: str
    size_bytes: int
    selectable: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "selectable": self.selectable,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ApiCodingContextFileList:
    workspace_label: str
    files: tuple[ApiCodingContextListedFile, ...]
    truncated: bool
    limits: dict[str, int]

    def to_dict(self) -> dict[str, object]:
        return {
            "workspace_label": self.workspace_label,
            "files": [item.to_dict() for item in self.files],
            "truncated": self.truncated,
            "limits": dict(self.limits),
        }


def build_api_coding_context(
    workspace_path: Path,
    *,
    prompt: str,
    context_mode: str = "selected_files",
    selected_files: list[str] | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> ApiCodingContext:
    """Build bounded, safe source context for a future API coding provider."""

    root = _workspace_root(workspace_path)
    mode = context_mode.strip().casefold()
    if mode not in SUPPORTED_CONTEXT_MODES:
        raise ValueError(f"unsupported API coding context mode: {context_mode}")
    limits = ApiCodingContextLimits(
        max_files=_positive_int(max_files, "max_files"),
        max_file_bytes=_positive_int(max_file_bytes, "max_file_bytes"),
        max_total_bytes=_positive_int(max_total_bytes, "max_total_bytes"),
    )
    candidates = _selected_candidates(root, selected_files) if selected_files else _default_candidates(root, mode)
    tree_summary, tree_excluded, tree_truncated = _tree_summary(root, limits.max_tree_entries)
    files, excluded, total_bytes, body_truncated = _include_files(root, candidates, limits)
    return ApiCodingContext(
        workspace_label=root.name or "workspace",
        context_mode=mode,
        prompt_preview=_preview(prompt),
        files=tuple(files),
        tree_summary=tuple(tree_summary if mode == "project_summary" else ()),
        excluded_files=tuple(tree_excluded + excluded),
        limits=limits,
        total_bytes=total_bytes,
        truncated=tree_truncated or body_truncated,
    )


def list_api_coding_context_files(
    workspace_path: Path,
    *,
    max_files: int = DEFAULT_TREE_ENTRY_LIMIT,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> ApiCodingContextFileList:
    """List safe relative project files for UI selection without reading bodies into the response."""

    root = _workspace_root(workspace_path)
    max_count = _positive_int(max_files, "max_files")
    max_bytes = _positive_int(max_file_bytes, "max_file_bytes")
    files: list[ApiCodingContextListedFile] = []
    truncated = False
    for entry in _walk_context_entries(root):
        if len(files) >= max_count:
            truncated = True
            break
        rel_path, path, generated_dir = entry
        if generated_dir:
            files.append(ApiCodingContextListedFile(
                path=rel_path,
                kind="generated_folder",
                size_bytes=0,
                selectable=False,
                reason="generated_folder",
            ))
            continue
        if not path.is_file():
            continue
        size = _safe_size(path)
        reason = _list_file_reason(root, rel_path, path, max_bytes)
        files.append(ApiCodingContextListedFile(
            path=rel_path,
            kind=_listed_kind(rel_path, reason),
            size_bytes=size,
            selectable=reason is None,
            reason=reason,
        ))
    return ApiCodingContextFileList(
        workspace_label=root.name or "workspace",
        files=tuple(files),
        truncated=truncated,
        limits={"max_files": max_count, "max_file_bytes": max_bytes},
    )


def _workspace_root(workspace_path: Path) -> Path:
    if not isinstance(workspace_path, Path):
        raise TypeError("workspace_path must be a Path")
    root = workspace_path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace_path must be an existing directory")
    return root


def _walk_context_entries(root: Path) -> Iterable[tuple[str, Path, bool]]:
    def walk(base: Path) -> Iterable[tuple[str, Path, bool]]:
        try:
            entries = sorted(base.iterdir(), key=lambda item: _relative(root, item).casefold())
        except OSError:
            return
        for path in entries:
            rel_path = _relative(root, path)
            name = path.name.casefold()
            if path.is_dir() and name in GENERATED_DIRS:
                yield f"{rel_path}/", path, True
                continue
            yield rel_path, path, False
            if path.is_dir() and not path.is_symlink():
                yield from walk(path)

    yield from walk(root)


def _safe_size(path: Path) -> int:
    try:
        return min(2_147_483_647, max(0, path.stat().st_size))
    except OSError:
        return 0


def _list_file_reason(root: Path, rel_path: str, path: Path, max_file_bytes: int) -> str | None:
    reason = _file_safety_reason(root, rel_path, path, max_file_bytes)
    if reason == "sensitive_path":
        return "secret_path"
    if reason is not None:
        return reason
    try:
        data = path.read_bytes()
    except OSError:
        return "unreadable"
    if _looks_binary(data):
        return "binary_file"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "non_utf8_text"
    if _contains_secret(text):
        return "secret_detected"
    return None


def _listed_kind(rel_path: str, reason: str | None) -> str:
    if reason in {"secret_path", "secret_detected"}:
        return "secret"
    if reason == "unsupported_file_type":
        return "unsupported_type"
    if reason == "max_file_bytes_exceeded":
        return "oversized"
    if reason == "hidden_path":
        return "hidden"
    return _kind_for_path(rel_path)


def _positive_int(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _preview(prompt: str) -> str:
    text = prompt if isinstance(prompt, str) else ""
    return text.replace("\x00", "")[:PROMPT_PREVIEW_CHARS]


def _selected_candidates(root: Path, selected_files: Iterable[str] | None) -> list[str]:
    del root
    seen: set[str] = set()
    candidates: list[str] = []
    for item in selected_files or ():
        path = str(item)
        if path not in seen:
            seen.add(path)
            candidates.append(path)
    return candidates


def _default_candidates(root: Path, mode: str) -> list[str]:
    candidates: list[str] = []
    if mode == "project_summary":
        for name in KEY_CONFIG_FILES:
            if (root / name).is_file():
                candidates.append(name)
        return candidates

    if (root / "platformio.ini").is_file():
        candidates.append("platformio.ini")
    if (root / "README.md").is_file():
        candidates.append("README.md")
    for folder in DEFAULT_CONTEXT_ROOTS:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*"), key=lambda item: _relative(root, item)):
            if path.is_file():
                candidates.append(_relative(root, path))
    return candidates


def _include_files(
    root: Path,
    candidates: list[str],
    limits: ApiCodingContextLimits,
) -> tuple[list[ApiCodingContextFile], list[ApiCodingContextExcludedFile], int, bool]:
    files: list[ApiCodingContextFile] = []
    excluded: list[ApiCodingContextExcludedFile] = []
    total_bytes = 0
    truncated = False
    seen: set[str] = set()
    for raw in candidates:
        path_text = str(raw)
        if path_text.casefold() in seen:
            continue
        seen.add(path_text.casefold())
        if len(files) >= limits.max_files:
            excluded.append(ApiCodingContextExcludedFile(_safe_display_path(path_text), "max_files_exceeded"))
            truncated = True
            continue

        resolved = _resolve_candidate(root, path_text)
        if isinstance(resolved, ApiCodingContextExcludedFile):
            excluded.append(resolved)
            continue
        rel_path, path = resolved
        safety_reason = _file_safety_reason(root, rel_path, path, limits.max_file_bytes)
        if safety_reason is not None:
            excluded.append(ApiCodingContextExcludedFile(rel_path, safety_reason))
            if safety_reason.endswith("_exceeded"):
                truncated = True
            continue

        data = path.read_bytes()
        if _looks_binary(data):
            excluded.append(ApiCodingContextExcludedFile(rel_path, "binary_file"))
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            excluded.append(ApiCodingContextExcludedFile(rel_path, "non_utf8_text"))
            continue
        if _contains_secret(content):
            excluded.append(ApiCodingContextExcludedFile(rel_path, "secret_detected"))
            continue
        if total_bytes + len(data) > limits.max_total_bytes:
            excluded.append(ApiCodingContextExcludedFile(rel_path, "max_total_bytes_exceeded"))
            truncated = True
            continue

        files.append(ApiCodingContextFile(
            path=rel_path,
            kind=_kind_for_path(rel_path),
            content=content,
            size_bytes=len(data),
            truncated=False,
        ))
        total_bytes += len(data)
    return files, excluded, total_bytes, truncated


def _resolve_candidate(root: Path, path_text: str) -> tuple[str, Path] | ApiCodingContextExcludedFile:
    path_error = _path_text_error(path_text)
    if path_error is not None:
        return ApiCodingContextExcludedFile(_safe_display_path(path_text), path_error)
    target = root.joinpath(*path_text.split("/"))
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        return ApiCodingContextExcludedFile(path_text, "not_found")
    try:
        resolved.relative_to(root)
    except ValueError:
        return ApiCodingContextExcludedFile(path_text, "symlink_escape")
    return path_text, resolved


def _file_safety_reason(root: Path, rel_path: str, path: Path, max_file_bytes: int) -> str | None:
    del root
    parts = tuple(part.casefold() for part in rel_path.split("/"))
    if any(part in GENERATED_DIRS for part in parts):
        return "generated_or_ignored_path"
    if _sensitive_name(rel_path):
        return "sensitive_path"
    if any(part.startswith(".") for part in parts):
        return "hidden_path"
    if not path.is_file():
        return "not_a_regular_file"
    try:
        size = path.stat().st_size
    except OSError:
        return "unreadable"
    if size > max_file_bytes:
        return "max_file_bytes_exceeded"
    if path.suffix.casefold() and path.suffix.casefold() not in TEXT_SUFFIXES:
        return "unsupported_file_type"
    return None


def _path_text_error(path_text: str) -> str | None:
    if not path_text or "\x00" in path_text:
        return "invalid_path"
    if "\\" in path_text:
        return "absolute_or_unsafe_path" if path_text.startswith("\\\\") else "unsafe_path_separator"
    windows = PureWindowsPath(path_text)
    posix = PurePosixPath(path_text)
    if path_text.startswith("/") or posix.is_absolute() or windows.is_absolute() or windows.drive:
        return "absolute_path"
    parts = path_text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "path_traversal"
    if any(":" in part or part.endswith((" ", ".")) for part in parts):
        return "unsafe_path_segment"
    return None


def _tree_summary(root: Path, max_entries: int) -> tuple[list[str], list[ApiCodingContextExcludedFile], bool]:
    summary: list[str] = []
    excluded: list[ApiCodingContextExcludedFile] = []
    truncated = False
    for path in sorted(root.rglob("*"), key=lambda item: _relative(root, item)):
        rel_path = _relative(root, path)
        parts = tuple(part.casefold() for part in rel_path.split("/"))
        ignored = next((part for part in parts if part in GENERATED_DIRS), None)
        if ignored is not None:
            if rel_path == ignored or f"/{ignored}/" in f"/{rel_path}/":
                excluded.append(ApiCodingContextExcludedFile(rel_path, "generated_or_ignored_path"))
            continue
        if _sensitive_name(rel_path):
            excluded.append(ApiCodingContextExcludedFile(rel_path, "sensitive_path"))
            continue
        if any(part.startswith(".") for part in parts):
            excluded.append(ApiCodingContextExcludedFile(rel_path, "hidden_path"))
            continue
        if len(summary) >= max_entries:
            excluded.append(ApiCodingContextExcludedFile(rel_path, "tree_summary_limit_exceeded"))
            truncated = True
            continue
        marker = "/" if path.is_dir() else ""
        summary.append(f"{rel_path}{marker}")
    return summary, excluded, truncated


def _sensitive_name(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1].casefold()
    return (
        name in SENSITIVE_EXACT_NAMES
        or any(name.startswith(prefix) for prefix in SENSITIVE_PREFIXES)
        or any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES)
    )


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    controls = _BINARY_CONTROL_RE.findall(data)
    return b"\x00" in data or len(controls) / len(data) > 0.01


def _contains_secret(content: str) -> bool:
    return any(pattern.search(content) for pattern in SECRET_PATTERNS)


def _kind_for_path(rel_path: str) -> str:
    suffix = PurePosixPath(rel_path).suffix.casefold()
    if suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".ino"}:
        return "cpp"
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx"}:
        return "javascript"
    if suffix in {".ts", ".tsx"}:
        return "typescript"
    if suffix in {".json"}:
        return "json"
    if suffix in {".md"}:
        return "markdown"
    if suffix in {".ini", ".toml", ".yaml", ".yml"}:
        return "config"
    return "text"


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_display_path(value: str) -> str:
    return value.replace("\\", "/")[:240] or "<invalid>"


__all__ = [
    "ApiCodingContext",
    "ApiCodingContextExcludedFile",
    "ApiCodingContextFile",
    "ApiCodingContextFileList",
    "ApiCodingContextListedFile",
    "ApiCodingContextLimits",
    "build_api_coding_context",
    "list_api_coding_context_files",
]
