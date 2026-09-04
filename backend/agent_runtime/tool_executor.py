"""Forge Agent V3 tool executor with overlay-staged workspace semantics."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Callable, Mapping

from .capabilities import CapabilityGrant, ToolPolicyEngine
from .skills import SkillRegistry
from .tool_contracts import RuntimeClassification, ToolCall, ToolName
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path

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
    ) -> None:
        self.write_root = write_root.resolve(strict=True)
        self.read_root = (read_root or write_root).resolve(strict=True)
        self.policy = policy
        self.policy_engine = policy_engine
        self.capability_grant = capability_grant
        self.service_handlers = dict(service_handlers or {})
        self.skill_registry = skill_registry or SkillRegistry(self.read_root)
        self.current_plan: tuple[dict[str, str], ...] = ()

    def validate(self, call: ToolCall, *, consume: bool = False) -> None:
        root = self._validation_root(call)
        if self.policy_engine is not None:
            if self.capability_grant is None:
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "capability_required")
            self.policy_engine.validate_call(
                self.capability_grant,
                call,
                root,
                policy=self.policy,
                consume=consume,
            )
            return
        self.policy.validate_call(call, root)

    def execute(self, call: ToolCall) -> object:
        self.validate(call, consume=True)
        args = dict(call.arguments)
        if call.tool is ToolName.LIST_FILES:
            relative = str(args.get("path", "."))
            root = self.policy.resolve_path(self.read_root, relative, must_exist=True, allow_directory=True)
            prefix = root.relative_to(self.read_root).as_posix()
            if prefix == ".":
                prefix = ""
            return tuple(path for path in self._overlay_files() if not prefix or path == prefix or path.startswith(prefix + "/"))

        if call.tool is ToolName.GLOB_FILES:
            relative = str(args.get("path", "."))
            self.policy.resolve_path(self.read_root, relative, must_exist=True, allow_directory=True)
            pattern = str(args["pattern"])
            prefix = "" if relative == "." else relative.rstrip("/") + "/"
            return tuple(path for path in self._overlay_files() if path.startswith(prefix) and fnmatch.fnmatch(path[len(prefix):], pattern))[:200]

        if call.tool is ToolName.GREP_SEARCH:
            relative = str(args.get("path", "."))
            self.policy.resolve_path(self.read_root, relative, must_exist=True, allow_directory=True)
            query = str(args["query"]).casefold()
            limit = int(args.get("max_results", 50))
            prefix = "" if relative == "." else relative.rstrip("/") + "/"
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
                        matches.append({"path": path, "line": line_no, "text": line[:500]})
                        if len(matches) >= limit:
                            return tuple(matches)
            return tuple(matches)

        if call.tool is ToolName.READ_FILE:
            relative = str(args["path"])
            return self._read_overlay_text(relative)

        if call.tool is ToolName.WRITE_FILE:
            relative = str(args["path"])
            content = str(args["content"])
            existed = self._overlay_exists(relative)
            prior = self._overlay_path(relative)
            if prior is not None and prior.is_file():
                active_bytes = prior.read_bytes()
                if b"\r\n" in active_bytes and "\r\n" not in content:
                    content = content.replace("\n", "\r\n")
            self._write_stage(relative, content)
            return {"created": not existed, "modified": existed, "bytes": len(content.encode("utf-8")), "path": relative}

        if call.tool is ToolName.EDIT_FILE_SIMPLE:
            relative = str(args["path"])
            old_text, new_text = str(args["old_text"]), str(args["new_text"])
            current = self._read_overlay_text(relative)
            if not old_text or current.count(old_text) != 1:
                raise ValueError("edit_match_must_be_exactly_one")
            updated = current.replace(old_text, new_text, 1)
            self.policy.validate_content(relative, updated)
            self._write_stage(relative, updated)
            return {"modified": True, "bytes": len(updated.encode("utf-8")), "path": relative}

        if call.tool is ToolName.UPDATE_PLAN:
            steps = tuple({"text": str(item["text"]), "status": str(item["status"])} for item in args["steps"])  # type: ignore[index]
            self.current_plan = steps
            return {"steps": list(steps)}

        if call.tool is ToolName.LOAD_SKILL:
            name = str(args["name"])
            return {"name": name, "content": self.skill_registry.load(name)}

        handler = self.service_handlers.get(call.tool)
        if handler is not None:
            return handler(args)

        if call.tool in {ToolName.MEMORY_SEARCH, ToolName.SPAWN_SUBAGENT, ToolName.BUILD_FIRMWARE, ToolName.RUN_COMMAND}:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_host_handler_unavailable")
        raise PermissionError("tool_not_executable")

    def _overlay_files(self) -> tuple[str, ...]:
        paths: set[str] = set()
        for root in (self.read_root, self.write_root):
            for item in root.rglob("*"):
                if not item.is_file() or item.is_symlink():
                    continue
                rel = item.relative_to(root).as_posix()
                if is_sensitive_path(rel) or any(part.startswith(".") for part in Path(rel).parts):
                    continue
                paths.add(rel)
        return tuple(sorted(paths))

    def _overlay_exists(self, relative: str) -> bool:
        return self._overlay_path(relative) is not None

    def _overlay_path(self, relative: str) -> Path | None:
        stage_candidate = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        if stage_candidate.exists() and stage_candidate.is_file() and not stage_candidate.is_symlink():
            return stage_candidate
        active_candidate = self.policy.resolve_path(self.read_root, relative, must_exist=False)
        if active_candidate.exists() and active_candidate.is_file() and not active_candidate.is_symlink():
            return active_candidate
        return None

    def _read_overlay_text(self, relative: str) -> str:
        if is_sensitive_path(relative):
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
        path = self._overlay_path(relative)
        if path is None:
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "path_missing")
        if path.stat().st_size > self.policy.max_file_bytes:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "file_too_large")
        data = path.read_bytes()
        if b"\x00" in data:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "binary_content_forbidden")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "utf8_text_required") from exc

    def _write_stage(self, relative: str, content: str) -> None:
        self.policy.validate_content(relative, content)
        path = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = self.policy.resolve_path(self.write_root, relative, must_exist=False)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

    def _validation_root(self, call: ToolCall) -> Path:
        # Capability grants bind to the immutable active project. Tool execution
        # still redirects every mutation into the stage overlay.
        return self.read_root
