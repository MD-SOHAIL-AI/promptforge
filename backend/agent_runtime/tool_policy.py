"""ForgeX-owned permission and path/content policy for staged agent tools."""

from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Mapping

from .tool_contracts import RuntimeClassification, ToolCall, ToolName


_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_SENSITIVE_NAMES = {
    ".env", ".env.local", ".npmrc", ".pypirc", "credentials", "credentials.json",
    "secrets.json", "id_rsa", "id_ed25519", "known_hosts",
}
_SENSITIVE_PARTS = ("credential", "secret", "token", "private_key", "apikey", "api_key")
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ToolPolicyError(ValueError):
    def __init__(self, classification: RuntimeClassification, reason: str) -> None:
        self.classification = classification
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class ToolPermissionPolicy:
    policy_id: str = "forgex-tool-runtime-default-v1"
    decisions: Mapping[ToolName, str] = field(default_factory=lambda: {
        ToolName.LIST_FILES: "allow",
        ToolName.GLOB_FILES: "allow",
        ToolName.GREP_SEARCH: "allow",
        ToolName.READ_FILE: "allow",
        ToolName.WRITE_FILE: "ask",
        ToolName.EDIT_FILE_SIMPLE: "ask",
        ToolName.UPDATE_PLAN: "allow",
        ToolName.LOAD_SKILL: "allow",
        ToolName.MEMORY_SEARCH: "allow",
        ToolName.SPAWN_SUBAGENT: "deny",
        ToolName.BUILD_FIRMWARE: "deny",
        ToolName.RUN_COMMAND: "deny",
    })
    allowed_extensions: frozenset[str] = frozenset({
        ".c", ".cc", ".cpp", ".css", ".h", ".hpp", ".html", ".ini", ".ino", ".js", ".json",
        ".md", ".mjs", ".py", ".rs", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
    })
    max_file_bytes: int = 512_000
    max_content_bytes: int = 128_000

    @property
    def provider_allowed_tools(self) -> tuple[str, ...]:
        return tuple(tool.value for tool in ToolName)

    def to_provider_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "staged_writes_only": True,
            "active_workspace_write": "deny",
            "external_read": "deny",
            "external_write": "deny",
            "shell": "deny",
            "network": "deny",
            "install_dependencies": "deny",
            "apply_patch": "staged_only",
            "build": "capability_gated",
            "flash": "deny",
            "max_content_bytes": self.max_content_bytes,
        }

    def validate_call(self, call: ToolCall, root: Path) -> None:
        decision = self.decisions.get(call.tool, "deny")
        if decision == "deny":
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_not_provider_executable")
        if decision == "ask":
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_requires_user_approval")

        args = dict(call.arguments)
        if call.tool is ToolName.LIST_FILES:
            self._require_keys(args, optional={"path"})
            self.resolve_path(root, self._string(args.get("path", ".")), must_exist=True, allow_directory=True)
            return
        if call.tool is ToolName.GLOB_FILES:
            self._require_keys(args, required={"pattern"}, optional={"path"})
            self._string(args["pattern"])
            self.resolve_path(root, self._string(args.get("path", ".")), must_exist=True, allow_directory=True)
            return
        if call.tool is ToolName.GREP_SEARCH:
            self._require_keys(args, required={"query"}, optional={"path", "max_results"})
            self._string(args["query"])
            self.resolve_path(root, self._string(args.get("path", ".")), must_exist=True, allow_directory=True)
            max_results = args.get("max_results", 50)
            if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 200:
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return
        if call.tool is ToolName.READ_FILE:
            self._require_keys(args, required={"path"})
            path = self._string(args["path"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
            self.resolve_path(root, path, must_exist=False)
            return
        if call.tool is ToolName.WRITE_FILE:
            self._require_keys(args, required={"path", "content"})
            path, content = self._string(args["path"]), self._string(args["content"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_write_forbidden")
            self.resolve_path(root, path, must_exist=False)
            self.validate_content(path, content)
            return
        if call.tool is ToolName.EDIT_FILE_SIMPLE:
            self._require_keys(args, required={"path", "old_text", "new_text"})
            path = self._string(args["path"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
            self.resolve_path(root, path, must_exist=False)
            self.validate_content(path, self._string(args["new_text"]))
            self._string(args["old_text"])
            return
        if call.tool is ToolName.UPDATE_PLAN:
            self._require_keys(args, required={"steps"})
            steps = args["steps"]
            if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            for step in steps:
                if not isinstance(step, Mapping) or set(step) != {"text", "status"}:
                    raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
                text, status = step["text"], step["status"]
                if not isinstance(text, str) or not text.strip() or len(text) > 300 or status not in {"pending", "in_progress", "completed"}:
                    raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return
        if call.tool is ToolName.LOAD_SKILL:
            self._require_keys(args, required={"name"})
            name = self._string(args["name"])
            if not name or len(name) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in name):
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return
        if call.tool is ToolName.MEMORY_SEARCH:
            self._require_keys(args, required={"query"}, optional={"limit"})
            self._string(args["query"])
            limit = args.get("limit", 5)
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return
        if call.tool is ToolName.SPAWN_SUBAGENT:
            self._require_keys(args, required={"role", "task"})
            role = self._string(args["role"]).casefold()
            task = self._string(args["task"])
            if role not in {"explore", "review", "verify"} or not task.strip() or len(task) > 4000:
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return
        if call.tool is ToolName.BUILD_FIRMWARE:
            self._require_keys(args, optional={"environment"})
            if "environment" in args:
                self._string(args["environment"])
            return
        if call.tool is ToolName.RUN_COMMAND:
            self._require_keys(args, required={"argv"}, optional={"timeout_seconds"})
            argv = args["argv"]
            if not isinstance(argv, list) or not argv or len(argv) > 32 or any(not isinstance(item, str) or not item or len(item) > 512 for item in argv):
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            timeout = args.get("timeout_seconds", 60)
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 1 <= float(timeout) <= 180:
                raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")
            return

    def resolve_path(
        self,
        root: Path,
        relative_path: str,
        *,
        must_exist: bool,
        allow_directory: bool = False,
    ) -> Path:
        root = root.resolve(strict=True)
        _reject_link_or_reparse(root)
        normalized = _validate_relative_path(relative_path)
        candidate = root if normalized == "." else root.joinpath(*normalized.split("/"))
        parent = candidate if candidate == root else candidate.parent
        if not parent.exists():
            if must_exist:
                raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "parent_directory_missing")
            ancestor = parent
            while ancestor != root and not ancestor.exists():
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "parent_directory_missing")
            _validate_existing_components(root, ancestor)
        else:
            if not parent.is_dir():
                raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "parent_directory_missing")
            _validate_existing_components(root, parent)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "staging_escape") from exc
        if candidate.exists() or candidate.is_symlink():
            _reject_link_or_reparse(candidate)
            if must_exist and not allow_directory and not candidate.is_file():
                raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "regular_file_required")
            if allow_directory and not candidate.is_dir():
                raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "directory_required")
        elif must_exist:
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "path_missing")
        if candidate != root and not allow_directory and candidate.suffix.casefold() not in self.allowed_extensions:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "extension_not_allowed")
        return candidate

    def validate_content(self, path: str, content: str) -> None:
        encoded = content.encode("utf-8")
        if b"\x00" in encoded:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "binary_content_forbidden")
        if len(encoded) > self.max_content_bytes or len(encoded) > self.max_file_bytes:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "content_too_large")

    @staticmethod
    def _string(value: object) -> str:
        if not isinstance(value, str):
            raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_argument_must_be_string")
        return value

    @staticmethod
    def _require_keys(args: Mapping[str, object], *, required: set[str] | None = None, optional: set[str] | None = None) -> None:
        required = required or set()
        allowed = required | (optional or set())
        if not required.issubset(args) or set(args) - allowed:
            raise ToolPolicyError(RuntimeClassification.PROVIDER_INVALID, "tool_arguments_invalid")


def product_agent_policy(*, max_bytes_per_file: int = 128_000) -> ToolPermissionPolicy:
    """Default-deny policy; model writes are allowed only into a staged ChangeSet."""

    bounded = max(1, min(int(max_bytes_per_file), 512_000))
    return ToolPermissionPolicy(
        policy_id="forgex-product-agent-v1",
        decisions={
            ToolName.LIST_FILES: "allow",
            ToolName.GLOB_FILES: "allow",
            ToolName.GREP_SEARCH: "allow",
            ToolName.READ_FILE: "allow",
            ToolName.WRITE_FILE: "allow",
            ToolName.EDIT_FILE_SIMPLE: "allow",
            ToolName.UPDATE_PLAN: "allow",
            ToolName.LOAD_SKILL: "allow",
            ToolName.MEMORY_SEARCH: "allow",
            ToolName.SPAWN_SUBAGENT: "allow",
            ToolName.BUILD_FIRMWARE: "allow",
            ToolName.RUN_COMMAND: "deny",
            },
        max_file_bytes=bounded,
        max_content_bytes=bounded,
    )


def subagent_policy(*, allow_build: bool = False, max_bytes_per_file: int = 128_000) -> ToolPermissionPolicy:
    """Read-only child-agent policy; verify may additionally invoke the trusted build service."""
    bounded = max(1, min(int(max_bytes_per_file), 512_000))
    decisions = {tool: "deny" for tool in ToolName}
    for tool in (
        ToolName.LIST_FILES, ToolName.GLOB_FILES, ToolName.GREP_SEARCH, ToolName.READ_FILE,
        ToolName.UPDATE_PLAN, ToolName.LOAD_SKILL, ToolName.MEMORY_SEARCH,
    ):
        decisions[tool] = "allow"
    if allow_build:
        decisions[ToolName.BUILD_FIRMWARE] = "allow"
    return ToolPermissionPolicy(
        policy_id="forgex-subagent-readonly-v1",
        decisions=decisions,
        max_file_bytes=bounded,
        max_content_bytes=bounded,
    )


def is_sensitive_path(value: str) -> bool:
    parts = value.replace("\\", "/").casefold().split("/")
    return any(part in _SENSITIVE_NAMES or any(marker in part for marker in _SENSITIVE_PARTS) for part in parts)


def _validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "path_format_unsafe")
    if not value.isascii() or unicodedata.normalize("NFKC", value) != value:
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "unicode_path_unsupported")
    windows = PureWindowsPath(value)
    if windows.drive or windows.is_absolute() or Path(value).is_absolute() or value.startswith("/"):
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "absolute_path_forbidden")
    if value == ".":
        return value
    parts = value.split("/")
    if any(part in {"", ".."} or part.startswith(".") or part.endswith((" ", ".")) for part in parts):
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "path_segment_unsafe")
    for part in parts:
        device = part.split(".", 1)[0].upper()
        if device in _RESERVED_WINDOWS_NAMES or ":" in part:
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "reserved_path_forbidden")
    return "/".join(parts)


def _validate_existing_components(root: Path, target: Path) -> None:
    current = root
    relative = target.relative_to(root)
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _reject_link_or_reparse(current)


def _reject_link_or_reparse(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "path_stat_failed") from exc
    if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE):
        raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "link_or_reparse_forbidden")
