"""Strict unified diff staging for ForgeX bridge patches."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .diff_service import MAX_FILE_BYTES, contains_ignored_path_part, hash_file, is_safe_relative_path


class PatchApplyEngineError(ValueError):
    code = "PATCH_APPLY_ENGINE_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class PatchApplyStagedFile:
    path: str
    operation: str
    target: Path
    before_hash: str | None
    after_hash: str | None
    content: bytes | None


@dataclass(frozen=True, slots=True)
class ParsedPatchFile:
    path: str
    hunks: tuple[tuple[int, tuple[str, ...]], ...]


class PatchApplyEngine:
    """Parse and stage only ForgeX-generated git unified diffs."""

    def __init__(self, *, max_file_bytes: int = MAX_FILE_BYTES) -> None:
        self.max_file_bytes = max_file_bytes

    def stage(
        self,
        patch_text: str,
        *,
        workspace_root: str | Path,
        files_to_create: tuple[str, ...],
        files_to_modify: tuple[str, ...],
        files_to_delete: tuple[str, ...],
    ) -> tuple[PatchApplyStagedFile, ...]:
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise PatchApplyEngineError("Active workspace root is missing.", {"conflict": "workspace_drift"})
        operation_by_path: dict[str, str] = {}
        for path in files_to_create:
            operation_by_path[path] = "create"
        for path in files_to_modify:
            operation_by_path[path] = "modify"
        for path in files_to_delete:
            operation_by_path[path] = "delete"
        if not operation_by_path:
            raise PatchApplyEngineError("Patch contains no supported file operations.", {"conflict": "parse_error"})

        parsed = self.parse(patch_text)
        parsed_paths = {item.path for item in parsed}
        expected_paths = set(operation_by_path)
        if parsed_paths != expected_paths:
            raise PatchApplyEngineError("Patch paths do not match preflight file list.", {"conflict": "parse_error"})

        staged: list[PatchApplyStagedFile] = []
        for item in parsed:
            operation = operation_by_path[item.path]
            target = self._safe_target(root, item.path)
            before_hash = hash_file(target) if target.exists() and target.is_file() and not target.is_symlink() else None
            if operation == "create":
                if target.exists():
                    raise PatchApplyEngineError("Create target already exists.", {"path": item.path, "conflict": "target_changed"})
                original: list[str] = []
            elif operation in {"modify", "delete"}:
                if not target.exists() or not target.is_file() or target.is_symlink():
                    raise PatchApplyEngineError("Patch target is not a regular file.", {"path": item.path, "conflict": "target_missing"})
                if target.stat().st_size > self.max_file_bytes:
                    raise PatchApplyEngineError("Patch target is too large for safe apply.", {"path": item.path, "conflict": "large_file_unsupported"})
                try:
                    original = target.read_text(encoding="utf-8").splitlines(keepends=True)
                except UnicodeDecodeError as exc:
                    raise PatchApplyEngineError("Patch target is not UTF-8 text.", {"path": item.path, "conflict": "binary_unsupported"}) from exc
            else:
                raise PatchApplyEngineError("Patch operation is unsupported.", {"path": item.path, "conflict": "parse_error"})

            output = self._apply_hunks(item.path, original, item.hunks)
            if operation == "delete":
                if "".join(output) != "":
                    raise PatchApplyEngineError("Delete patch did not stage an empty file.", {"path": item.path, "conflict": "parse_error"})
                staged.append(PatchApplyStagedFile(item.path, operation, target, before_hash, None, None))
                continue

            content = "".join(output).encode("utf-8")
            if len(content) > self.max_file_bytes:
                raise PatchApplyEngineError("Staged output is too large for safe apply.", {"path": item.path, "conflict": "large_file_unsupported"})
            after_hash = self._hash_bytes(content)
            staged.append(PatchApplyStagedFile(item.path, operation, target, before_hash, after_hash, content))
        return tuple(staged)

    def parse(self, patch_text: str) -> tuple[ParsedPatchFile, ...]:
        if not patch_text.strip() or "diff --git " not in patch_text:
            raise PatchApplyEngineError("Patch does not contain supported git diff headers.", {"conflict": "parse_error"})
        unsupported_markers = (
            "GIT binary patch",
            "Binary files ",
            "# Binary or unsupported diff preview",
            "# ... [patch truncated]",
            "rename from ",
            "rename to ",
            "similarity index ",
            "dissimilarity index ",
            "Subproject commit ",
        )
        if any(marker in patch_text for marker in unsupported_markers):
            raise PatchApplyEngineError("Patch contains unsupported changes.", {"conflict": "parse_error"})

        lines = patch_text.splitlines(keepends=True)
        files: list[ParsedPatchFile] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.startswith("diff --git "):
                index += 1
                continue
            match = re.match(r"^diff --git a/(.+?) b/(.+)\r?\n?$", line)
            if not match:
                raise PatchApplyEngineError("Patch contains an unsupported diff header.", {"conflict": "parse_error"})
            left, right = match.group(1), match.group(2)
            if left != right:
                raise PatchApplyEngineError("Patch renames are not supported.", {"path": right, "conflict": "parse_error"})
            self._validate_relative_path(right)
            index += 1
            section: list[str] = []
            while index < len(lines) and not lines[index].startswith("diff --git "):
                section.append(lines[index])
                index += 1
            files.append(ParsedPatchFile(right, self._parse_section(right, section)))
        if not files:
            raise PatchApplyEngineError("Patch contains no supported file sections.", {"conflict": "parse_error"})
        return tuple(files)

    def _parse_section(self, path: str, lines: list[str]) -> tuple[tuple[int, tuple[str, ...]], ...]:
        for line in lines:
            if line.startswith(("old mode ", "new mode ", "deleted file mode ", "new file mode ", "index ")):
                raise PatchApplyEngineError("Patch file mode metadata is not supported.", {"path": path, "conflict": "parse_error"})
        minus = next((line for line in lines if line.startswith("--- ")), None)
        plus = next((line for line in lines if line.startswith("+++ ")), None)
        if minus is None or plus is None:
            raise PatchApplyEngineError("Patch file headers are missing.", {"path": path, "conflict": "parse_error"})
        if not minus.startswith(f"--- a/{path}") or not plus.startswith(f"+++ b/{path}"):
            raise PatchApplyEngineError("Patch file headers do not match the diff path.", {"path": path, "conflict": "parse_error"})

        hunks: list[tuple[int, tuple[str, ...]]] = []
        index = 0
        while index < len(lines):
            header = lines[index]
            match = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", header)
            if not match:
                index += 1
                continue
            old_start = int(match.group(1))
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].startswith("@@ "):
                value = lines[index]
                if value in {"\n", "\r\n"}:
                    index += 1
                    continue
                if value.startswith((" ", "-", "+", "\\")):
                    body.append(value)
                    index += 1
                    continue
                break
            hunks.append((old_start, tuple(body)))
        if not hunks:
            raise PatchApplyEngineError("Patch section contains no supported hunks.", {"path": path, "conflict": "parse_error"})
        return tuple(hunks)

    def _apply_hunks(self, path: str, original: list[str], hunks: tuple[tuple[int, tuple[str, ...]], ...]) -> list[str]:
        output: list[str] = []
        cursor = 0
        for old_start, body in hunks:
            hunk_index = max(old_start - 1, 0)
            if hunk_index < cursor or hunk_index > len(original):
                raise PatchApplyEngineError("Patch hunk location is ambiguous.", {"path": path, "conflict": "parse_error"})
            output.extend(original[cursor:hunk_index])
            cursor = hunk_index
            for line in body:
                marker = line[:1]
                if marker == "\\":
                    continue
                content = line[1:]
                if marker == " ":
                    self._require_line(path, original, cursor, content)
                    output.append(content)
                    cursor += 1
                elif marker == "-":
                    self._require_line(path, original, cursor, content)
                    cursor += 1
                elif marker == "+":
                    output.append(content)
                else:
                    raise PatchApplyEngineError("Patch hunk line is unsupported.", {"path": path, "conflict": "parse_error"})
        output.extend(original[cursor:])
        return output

    def _require_line(self, path: str, original: list[str], index: int, expected: str) -> None:
        if index >= len(original) or original[index] != expected:
            raise PatchApplyEngineError("Patch context does not match the active workspace.", {"path": path, "conflict": "target_changed"})

    def _safe_target(self, root: Path, relative_path: str) -> Path:
        normalized = self._validate_relative_path(relative_path)
        target = (root / normalized).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise PatchApplyEngineError("Patch path escaped workspace root.", {"path": relative_path, "conflict": "path_unsafe"}) from exc
        parent = target.parent
        if parent.exists():
            try:
                parent.resolve().relative_to(root)
            except ValueError as exc:
                raise PatchApplyEngineError("Patch path follows a symlink outside the workspace.", {"path": relative_path, "conflict": "path_unsafe"}) from exc
        if target.exists() and target.is_symlink():
            raise PatchApplyEngineError("Patch target is a symlink.", {"path": relative_path, "conflict": "path_unsafe"})
        return target

    def _validate_relative_path(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/")
        if not is_safe_relative_path(normalized):
            raise PatchApplyEngineError("Patch path is unsafe.", {"path": relative_path, "conflict": "path_unsafe"})
        if contains_ignored_path_part(normalized):
            raise PatchApplyEngineError("Patch path targets an ignored folder.", {"path": relative_path, "conflict": "ignored_path"})
        return normalized

    def _hash_bytes(self, content: bytes) -> str:
        import hashlib

        return hashlib.sha256(content).hexdigest()


def replace_file_atomic(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".{target.name}.forgex-apply-{os.urandom(8).hex()}.tmp"
    try:
        temp.write_bytes(content)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
