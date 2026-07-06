"""Filesystem implementation of the small ForgeX-owned tool set."""

from __future__ import annotations

import os
from pathlib import Path

from .tool_contracts import RuntimeClassification, ToolCall, ToolName
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path


class ForgeXToolExecutor:
    def __init__(self, *, sandbox_root: Path, policy: ToolPermissionPolicy) -> None:
        self.sandbox_root = sandbox_root.resolve(strict=True)
        self.policy = policy

    def validate(self, call: ToolCall) -> None:
        self.policy.validate_call(call, self.sandbox_root)

    def execute(self, call: ToolCall) -> object:
        self.validate(call)
        args = dict(call.arguments)
        if call.tool is ToolName.LIST_FILES:
            root = self.policy.resolve_path(self.sandbox_root, str(args.get("path", ".")), must_exist=True, allow_directory=True)
            return tuple(
                item.relative_to(self.sandbox_root).as_posix()
                for item in sorted(root.rglob("*"))
                if item.is_file() and not item.is_symlink() and not is_sensitive_path(item.relative_to(self.sandbox_root).as_posix())
            )
        if call.tool is ToolName.READ_FILE:
            path = self.policy.resolve_path(self.sandbox_root, str(args["path"]), must_exist=True)
            if path.stat().st_size > self.policy.max_file_bytes:
                raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "file_too_large")
            data = path.read_bytes()
            if b"\x00" in data:
                raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "binary_content_forbidden")
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "utf8_text_required") from exc
        if call.tool is ToolName.WRITE_FILE:
            path = self.policy.resolve_path(self.sandbox_root, str(args["path"]), must_exist=False)
            content = str(args["content"])
            existed = path.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            path = self.policy.resolve_path(self.sandbox_root, str(args["path"]), must_exist=False)
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            return {"created": not existed, "modified": existed, "bytes": len(content.encode("utf-8"))}
        if call.tool is ToolName.EDIT_FILE_SIMPLE:
            path = self.policy.resolve_path(self.sandbox_root, str(args["path"]), must_exist=True)
            old_text, new_text = str(args["old_text"]), str(args["new_text"])
            current = path.read_text(encoding="utf-8")
            if not old_text or current.count(old_text) != 1:
                raise ValueError("edit_match_must_be_exactly_one")
            updated = current.replace(old_text, new_text, 1)
            self.policy.validate_content(path.relative_to(self.sandbox_root).as_posix(), updated, exact_smoke=False)
            with path.open("r+", encoding="utf-8", newline="") as handle:
                handle.seek(0)
                handle.write(updated)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            return {"modified": True, "bytes": len(updated.encode("utf-8"))}
        raise PermissionError("tool_not_executable")
