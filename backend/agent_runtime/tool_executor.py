"""Forge Agent V3 tool executor with overlay-staged workspace semantics."""

from __future__ import annotations

import difflib
import hashlib
import fnmatch
import os
import re
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Callable, Mapping

from .capabilities import CapabilityGrant, ToolPolicyEngine
from .skills import SkillRegistry
from .tool_contracts import RuntimeClassification, ToolCall, ToolName
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path
from backend.security import has_high_confidence_secret

ToolHandler = Callable[[Mapping[str, object]], object]


class ForgeXToolExecutor:
    """Execute model tools without giving direct write access to the active workspace.

    The active project is the immutable lower layer and ``write_root`` is the mutable
    upper layer. Reads prefer staged files, so subsequent model turns see their own
    edits. Build/command/memory handlers are injected by the trusted host.
    """

    def __init__(
        self,
        *,
        write_root: Path,
        policy: ToolPermissionPolicy,
        read_root: Path | None = None,
        policy_engine: ToolPolicyEngine | None = None,
        capability_grant: CapabilityGrant | None = None,
        service_handlers: Mapping[ToolName, ToolHandler] | None = None,
        skill_registry: SkillRegistry | None = None,
        environment: str | None = None,
    ) -> None:
        self.write_root = write_root.resolve(strict=True)
        self.read_root = (read_root or write_root).resolve(strict=True)
        self.policy = policy
        self.policy_engine = policy_engine
        self.capability_grant = capability_grant
        self.service_handlers = dict(service_handlers or {})
        self.skill_registry = skill_registry or SkillRegistry(self.read_root)
        self.current_plan: tuple[dict[str, str], ...] = ()
        self.environment = environment
        self.deleted_paths: set[str] = set()

    def validate(self, call: ToolCall, *, consume: bool = False) -> None:
        root = self._validation_root(call)
        if self.policy_engine is not None:
            if self.capability_grant is None:
                raise ToolPolicyError(
                    RuntimeClassification.POLICY_DENIED, "capability_required"
                )
            self.policy_engine.validate_call(
                self.capability_grant,
                call,
                root,
                policy=self.policy,
                consume=consume,
            )
        else:
            self.policy.validate_call(call, root)
        if call.tool in {ToolName.LIST_FILES, ToolName.GLOB_FILES, ToolName.GREP_SEARCH}:
            self._overlay_directory(str(call.arguments.get("path", ".")))

    def execute(self, call: ToolCall) -> object:
        self.validate(call, consume=True)
        args = dict(call.arguments)
        from .tool_registry import NEW_TOOLS, HOST_TOOLS
        if call.tool in NEW_TOOLS and NEW_TOOLS[call.tool].handler == "local":
            from .esp32_tools import local_tool
            return local_tool(self, call.tool, args)
        if call.tool is ToolName.LIST_FILES:
            relative = str(args.get("path", "."))
            prefix = self._overlay_directory(relative)
            return tuple(
                path
                for path in self._overlay_files()
                if not prefix or path == prefix or path.startswith(prefix + "/")
            )

        if call.tool is ToolName.GLOB_FILES:
            relative = str(args.get("path", "."))
            directory = self._overlay_directory(relative)
            pattern = str(args["pattern"])
            prefix = directory + "/" if directory else ""
            return tuple(
                path
                for path in self._overlay_files()
                if path.startswith(prefix)
                and fnmatch.fnmatch(path[len(prefix) :], pattern)
            )[:200]

        if call.tool is ToolName.GREP_SEARCH:
            relative = str(args.get("path", "."))
            directory = self._overlay_directory(relative)
            query = str(args["query"]).casefold()
            limit = int(args.get("max_results", 50))
            prefix = directory + "/" if directory else ""
            matches: list[dict[str, object]] = []
            for path in self._overlay_files():
                if prefix and not path.startswith(prefix):
                    continue
                try:
                    text = self._read_overlay_text(path)
                except (OSError, UnicodeError, ToolPolicyError):
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if query in line.casefold():
                        matches.append(
                            {"path": path, "line": line_no, "text": line[:500]}
                        )
                        if len(matches) >= limit:
                            return tuple(matches)
            return tuple(matches)

        if call.tool is ToolName.READ_FILE:
            relative = str(args["path"])
            text = self._read_overlay_text(relative)
            if "offset" not in args and "limit" not in args:
                return text
            lines = text.splitlines(keepends=True)
            offset = int(args.get("offset", 1))
            limit = int(args.get("limit", 200))
            selected = lines[offset - 1 : offset - 1 + limit]
            return {
                "path": relative,
                "offset": offset,
                "total_lines": len(lines),
                "next_offset": offset + len(selected) if offset + len(selected) <= len(lines) else None,
                "content": "".join(selected),
            }

        if call.tool is ToolName.DELETE_FILE:
            relative = self._mutation_relative(str(args["path"]))
            before = self._mutation_source(relative)
            self._delete_stage_file(relative)
            return {"path": relative, "change_type": "deleted", **_edit_evidence(relative, before, "")}

        if call.tool is ToolName.MOVE_FILE:
            relative = self._mutation_relative(str(args["path"]))
            destination = self.policy.resolve_path(
                self.read_root, str(args["destination"]), must_exist=False,
            ).resolve(strict=False).relative_to(self.read_root).as_posix()
            before = self._mutation_source(relative)
            if self._overlay_exists(destination):
                raise ValueError("move_destination_exists")
            self.policy.validate_content(destination, before)
            prior_deletions = set(self.deleted_paths)
            self._write_stage(destination, before)
            try:
                self._delete_stage_file(relative)
            except BaseException:
                # Destination did not exist in the overlay before this move.
                target = self.policy.resolve_path(self.write_root, destination, must_exist=True)
                target.unlink()
                self.deleted_paths = prior_deletions
                raise
            return {"path": relative, "destination": destination, "change_type": "moved"}

        if call.tool is ToolName.WRITE_FILE:
            relative = str(args["path"])
            content = str(args["content"])
            existed = self._overlay_exists(relative)
            prior = self._overlay_path(relative)
            if prior is not None and prior.is_file():
                active_bytes = prior.read_bytes()
                if b"\r\n" in active_bytes and "\r\n" not in content:
                    content = content.replace("\n", "\r\n")
            before = self._read_overlay_text(relative) if existed else ""
            changed = not existed or before != content
            if changed:
                self._write_stage(relative, content)
            return {
                "created": not existed,
                "modified": existed and changed,
                "bytes": len(content.encode("utf-8")),
                "path": relative,
                **_edit_evidence(relative, before, content),
            }

        if call.tool is ToolName.EDIT_FILE_SIMPLE:
            relative = str(args["path"])
            old_text, new_text = str(args["old_text"]), str(args["new_text"])
            current = self._read_overlay_text(relative)
            if not old_text or current.count(old_text) != 1:
                raise ValueError("edit_match_must_be_exactly_one")
            updated = current.replace(old_text, new_text, 1)
            changed = updated != current
            if changed:
                self._write_stage(relative, updated)
            return {
                "modified": changed,
                "bytes": len(updated.encode("utf-8")),
                "path": relative,
                **_edit_evidence(relative, current, updated),
            }

        if call.tool is ToolName.APPLY_PATCH:
            relative = str(args["path"])
            patch = str(args["patch"])
            current = self._read_overlay_text(relative)
            updated = _apply_unified_patch(current, patch, expected_path=relative)
            changed = updated != current
            if changed:
                self._write_stage(relative, updated)
            return {
                "modified": changed,
                "bytes": len(updated.encode("utf-8")),
                "path": relative,
                **_edit_evidence(relative, current, updated),
                "patch_hunks": patch.count("@@") // 2 if "@@" in patch else 0,
            }

        if call.tool is ToolName.UPDATE_PLAN:
            steps = tuple({"text": str(item["text"]), "status": str(item["status"]), **({"verification": str(item["verification"])} if "verification" in item else {})} for item in args["steps"])  # type: ignore[index]
            self.current_plan = steps
            return {"steps": list(steps)}

        if call.tool is ToolName.LOAD_SKILL:
            name = str(args["name"])
            return {"name": name, "content": self.skill_registry.load(name)}

        if call.tool is ToolName.PRESENT_FILES:
            presented: list[dict[str, object]] = []
            for item in args["files"]:  # type: ignore[index]
                assert isinstance(item, Mapping)
                relative = str(item["path"])
                if is_sensitive_path(relative):
                    raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
                path = self._overlay_path(relative)
                if path is None or not path.is_file() or path.is_symlink():
                    raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "present_file_missing")
                payload = path.read_bytes()
                presented.append({
                    "path": relative,
                    "description": str(item.get("description") or "")[:300],
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "staged": path.is_relative_to(self.write_root),
                })
            return {"files": presented, "count": len(presented)}

        handler = self.service_handlers.get(call.tool)
        if handler is not None:
            return handler(args)

        if call.tool in {
            ToolName.MEMORY_SEARCH,
            ToolName.SPAWN_SUBAGENT,
            ToolName.BUILD_FIRMWARE,
            ToolName.RUN_COMMAND,
            ToolName.SESSION_EVENT_READ,
            ToolName.SESSION_EVENT_SEARCH,
            ToolName.SESSION_TRACE,
        }:
            raise ToolPolicyError(
                RuntimeClassification.POLICY_DENIED, "tool_host_handler_unavailable"
            )
        if call.tool in HOST_TOOLS:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_host_handler_unavailable")
        raise PermissionError("tool_not_executable")

    def _overlay_directory(self, relative: str) -> str:
        directories = []
        for root in (self.read_root, self.write_root):
            candidate = self.policy.resolve_path(root, relative, must_exist=False, allow_directory=True)
            if candidate.is_dir():
                canonical = candidate.resolve(strict=True).relative_to(root).as_posix()
                directories.append("" if canonical == "." else canonical)
        if not directories:
            raise ToolPolicyError(RuntimeClassification.FILE_NOT_FOUND, "directory_missing")
        return directories[-1]

    def _overlay_files(self) -> tuple[str, ...]:
        paths: set[str] = set()
        for root in (self.read_root, self.write_root):
            for item in root.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                rel = item.relative_to(root).as_posix()
                if is_sensitive_path(rel) or any(
                    part.startswith(".") for part in Path(rel).parts
                ):
                    continue
                paths.add(rel)
        deleted_keys = {os.path.normcase(path) for path in self.deleted_paths}
        return tuple(sorted(path for path in paths if os.path.normcase(path) not in deleted_keys))

    def _overlay_exists(self, relative: str) -> bool:
        return self._overlay_path(relative) is not None

    def _overlay_path(self, relative: str) -> Path | None:
        if os.path.normcase(relative) in {os.path.normcase(path) for path in self.deleted_paths}:
            return None
        stage_candidate = self.policy.resolve_path(
            self.write_root, relative, must_exist=False
        )
        if (
            stage_candidate.exists()
            and stage_candidate.is_file()
            and not stage_candidate.is_symlink()
        ):
            return stage_candidate
        active_candidate = self.policy.resolve_path(
            self.read_root, relative, must_exist=False
        )
        if (
            active_candidate.exists()
            and active_candidate.is_file()
            and not active_candidate.is_symlink()
        ):
            return active_candidate
        return None

    def _read_overlay_text(self, relative: str) -> str:
        if is_sensitive_path(relative):
            raise ToolPolicyError(
                RuntimeClassification.POLICY_DENIED, "credential_read_forbidden"
            )
        path = self._overlay_path(relative)
        if path is None:
            raise ToolPolicyError(RuntimeClassification.FILE_NOT_FOUND, "path_missing")
        if path.stat().st_size > self.policy.max_file_bytes:
            raise ToolPolicyError(
                RuntimeClassification.CONTENT_INVALID, "file_too_large"
            )
        data = path.read_bytes()
        if b"\x00" in data:
            raise ToolPolicyError(
                RuntimeClassification.CONTENT_INVALID, "binary_content_forbidden"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolPolicyError(
                RuntimeClassification.CONTENT_INVALID, "utf8_text_required"
            ) from exc
        if has_high_confidence_secret(text):
            raise ToolPolicyError(
                RuntimeClassification.POLICY_DENIED, "secret_content_forbidden"
            )
        return text

    def _mutation_relative(self, relative: str) -> str:
        source = self._overlay_path(relative)
        if source is None:
            raise ValueError("mutation_source_missing")
        source = source.resolve(strict=True)
        root = self.write_root if source.is_relative_to(self.write_root) else self.read_root
        return source.relative_to(root).as_posix()

    def _mutation_source(self, relative: str) -> str:
        if not self._overlay_exists(relative):
            raise ValueError("mutation_source_missing")
        return self._read_overlay_text(relative)

    def _delete_stage_file(self, relative: str) -> None:
        # The host supplies a private stage, never the active project as write
        # root. Overlay deletions only hide the immutable lower-layer file.
        target = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        if target.exists():
            target.unlink()
        self.deleted_paths.add(relative)

    def _write_stage(self, relative: str, content: str) -> None:
        self.policy.validate_content(relative, content)
        self._validate_project_file_regression(relative, content)
        path = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".forgex-write-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            # Revalidate containment before publishing; failed writes leave the
            # prior staged content intact, never a partially truncated file.
            guard = self.policy_engine.publication_guard(self.capability_grant, self.read_root) if self.policy_engine and self.capability_grant else nullcontext()
            with guard:
                path = self.policy.resolve_path(self.write_root, relative, must_exist=False)
                os.replace(temporary, path)
                self.deleted_paths = {old for old in self.deleted_paths if os.path.normcase(old) != os.path.normcase(relative)}
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_project_file_regression(self, relative: str, content: str) -> None:
        if relative.replace("\\", "/").casefold() != "platformio.ini":
            return
        current = self._overlay_path(relative)
        if current is None or not current.is_file():
            return
        try:
            previous = current.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return
        if _has_platformio_environment(previous) and not _has_platformio_environment(content):
            raise ValueError("platformio_environment_section_required")

    def _validation_root(self, call: ToolCall) -> Path:
        # Capability grants bind to the immutable active project. Tool execution
        # still redirects every mutation into the stage overlay.
        return self.read_root


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
_PLATFORMIO_ENV_RE = re.compile(r"(?mi)^\s*\[\s*env:[^\]\r\n]+\]\s*$")


def _has_platformio_environment(content: str) -> bool:
    return _PLATFORMIO_ENV_RE.search(content) is not None


def _apply_unified_patch(current: str, patch: str, *, expected_path: str) -> str:
    """Apply a single-file unified diff with exact context matching.

    This is deliberately smaller than GNU patch: no fuzzy matching, renames,
    binary patches, path traversal, or multi-file patches.  If any context line
    does not match exactly the whole patch is rejected and the staged file is
    left untouched.
    """
    if not patch.strip():
        raise ValueError("patch_empty")
    raw_lines = patch.splitlines()
    index = 0
    header_paths: list[str] = []
    while index < len(raw_lines) and (
        raw_lines[index].startswith("--- ") or raw_lines[index].startswith("+++ ")
    ):
        token = raw_lines[index][4:].split("\t", 1)[0].strip()
        if token != "/dev/null":
            if token.startswith(("a/", "b/")):
                token = token[2:]
            header_paths.append(token.replace("\\", "/"))
        index += 1
    if header_paths and any(path != expected_path for path in header_paths):
        raise ValueError("patch_path_mismatch")
    if index >= len(raw_lines) or not raw_lines[index].startswith("@@"):
        raise ValueError("patch_hunk_missing")

    source = current.splitlines()
    newline = "\r\n" if "\r\n" in current else "\n"
    had_final_newline = current.endswith(("\n", "\r"))
    result: list[str] = []
    source_index = 0
    hunk_count = 0

    while index < len(raw_lines):
        match = _HUNK_RE.match(raw_lines[index])
        if match is None:
            raise ValueError("patch_hunk_header_invalid")
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        target_index = 0 if old_start == 0 else old_start - 1
        if target_index < source_index or target_index > len(source):
            raise ValueError("patch_hunk_range_invalid")
        result.extend(source[source_index:target_index])
        source_index = target_index
        index += 1
        consumed_old = 0
        produced_new = 0
        while index < len(raw_lines) and not raw_lines[index].startswith("@@"):
            line = raw_lines[index]
            if line == r"\ No newline at end of file":
                index += 1
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise ValueError("patch_line_invalid")
            marker, text = line[0], line[1:]
            if marker in {" ", "-"}:
                if source_index >= len(source) or source[source_index] != text:
                    raise ValueError("patch_context_mismatch")
                if marker == " ":
                    result.append(source[source_index])
                    produced_new += 1
                consumed_old += 1
                source_index += 1
            else:
                result.append(text)
                produced_new += 1
            index += 1
        if consumed_old != old_count or produced_new != new_count:
            raise ValueError("patch_hunk_count_mismatch")
        hunk_count += 1

    if hunk_count == 0:
        raise ValueError("patch_hunk_missing")
    result.extend(source[source_index:])
    updated = newline.join(result)
    if had_final_newline:
        updated += newline
    return updated


def _edit_evidence(path: str, before: str, after: str) -> dict[str, object]:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=path,
            tofile=path,
            lineterm="",
        )
    )
    return {
        "added": sum(
            line.startswith("+") and not line.startswith("+++") for line in lines
        ),
        "removed": sum(
            line.startswith("-") and not line.startswith("---") for line in lines
        ),
        "diff": "\n".join(lines)[:16000],
    }
