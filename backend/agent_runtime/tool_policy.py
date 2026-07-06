"""ForgeX-owned permission and path/content policy for sandbox tools."""

from __future__ import annotations

import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Mapping

from .tool_contracts import RuntimeClassification, ToolCall, ToolName


SMOKE_PATH = "FORGEX_TOOL_RUNTIME_SMOKE.txt"
SMOKE_CONTENT = "ForgeX-owned tool runtime smoke completed.\n"
API_SMOKE_PATH = "FORGEX_API_PROVIDER_SMOKE.txt"
API_SMOKE_CONTENT = "ForgeX real API provider smoke completed.\n"

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
        ToolName.READ_FILE: "allow",
        ToolName.WRITE_FILE: "ask",
        ToolName.EDIT_FILE_SIMPLE: "ask",
        ToolName.CREATE_REVIEW: "allow_after_validated_diff",
    })
    allowed_extensions: frozenset[str] = frozenset({
        ".c", ".cc", ".cpp", ".css", ".h", ".hpp", ".html", ".ini", ".ino", ".js", ".json",
        ".md", ".mjs", ".py", ".rs", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
    })
    max_file_bytes: int = 512_000
    max_content_bytes: int = 128_000
    expected_path: str | None = None
    expected_content: str | None = None
    automated_fake_qa: bool = False

    @property
    def provider_allowed_tools(self) -> tuple[str, ...]:
        # Review authority is orchestration-owned and is never offered to a provider.
        return tuple(tool.value for tool in ToolName if tool is not ToolName.CREATE_REVIEW)

    def to_provider_dict(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "sandbox_only": True,
            "active_workspace_write": "deny",
            "external_read": "deny",
            "external_write": "deny",
            "shell": "deny",
            "network": "deny",
            "install_dependencies": "deny",
            "apply_patch": "deny",
            "build": "deny",
            "flash": "deny",
            "max_content_bytes": self.max_content_bytes,
        }

    def validate_call(self, call: ToolCall, sandbox_root: Path) -> None:
        decision = self.decisions.get(call.tool, "deny")
        if call.tool is ToolName.CREATE_REVIEW or decision == "deny":
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_not_provider_executable")
        if decision == "ask" and not self.automated_fake_qa:
            raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "tool_requires_user_approval")

        args = dict(call.arguments)
        if call.tool is ToolName.LIST_FILES:
            self._require_keys(args, optional={"path"})
            self.resolve_path(sandbox_root, self._string(args.get("path", ".")), must_exist=True, allow_directory=True)
            return
        if call.tool is ToolName.READ_FILE:
            self._require_keys(args, required={"path"})
            path = self._string(args["path"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
            self.resolve_path(sandbox_root, path, must_exist=True)
            return
        if call.tool is ToolName.WRITE_FILE:
            self._require_keys(args, required={"path", "content"})
            path, content = self._string(args["path"]), self._string(args["content"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_write_forbidden")
            self.resolve_path(sandbox_root, path, must_exist=False)
            self.validate_content(path, content)
            return
        if call.tool is ToolName.EDIT_FILE_SIMPLE:
            self._require_keys(args, required={"path", "old_text", "new_text"})
            path = self._string(args["path"])
            if is_sensitive_path(path):
                raise ToolPolicyError(RuntimeClassification.POLICY_DENIED, "credential_read_forbidden")
            self.resolve_path(sandbox_root, path, must_exist=True)
            self.validate_content(path, self._string(args["new_text"]), exact_smoke=False)
            self._string(args["old_text"])

    def resolve_path(
        self,
        sandbox_root: Path,
        relative_path: str,
        *,
        must_exist: bool,
        allow_directory: bool = False,
    ) -> Path:
        root = sandbox_root.resolve(strict=True)
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
            raise ToolPolicyError(RuntimeClassification.PATH_UNSAFE, "sandbox_escape") from exc
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

    def validate_content(self, path: str, content: str, *, exact_smoke: bool = True) -> None:
        encoded = content.encode("utf-8")
        if b"\x00" in encoded:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "binary_content_forbidden")
        if len(encoded) > self.max_content_bytes or len(encoded) > self.max_file_bytes:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "content_too_large")
        if exact_smoke and self.expected_path is not None and path != self.expected_path:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "unexpected_smoke_filename")
        if exact_smoke and self.expected_content is not None and content != self.expected_content:
            raise ToolPolicyError(RuntimeClassification.CONTENT_INVALID, "unexpected_smoke_content")

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


def fake_smoke_policy() -> ToolPermissionPolicy:
    return ToolPermissionPolicy(
        policy_id="forgex-fake-provider-smoke-v1",
        decisions={
            ToolName.LIST_FILES: "allow",
            ToolName.READ_FILE: "allow",
            ToolName.WRITE_FILE: "allow",
            ToolName.EDIT_FILE_SIMPLE: "allow",
            ToolName.CREATE_REVIEW: "allow_after_validated_diff",
        },
        expected_path=SMOKE_PATH,
        expected_content=SMOKE_CONTENT,
        automated_fake_qa=True,
    )


def api_provider_smoke_policy() -> ToolPermissionPolicy:
    return ToolPermissionPolicy(
        policy_id="forgex-openai-provider-smoke-v1",
        decisions={
            ToolName.LIST_FILES: "allow",
            ToolName.READ_FILE: "allow",
            ToolName.WRITE_FILE: "allow",
            ToolName.EDIT_FILE_SIMPLE: "allow",
            ToolName.CREATE_REVIEW: "allow_after_validated_diff",
        },
        expected_path=API_SMOKE_PATH,
        expected_content=API_SMOKE_CONTENT,
        automated_fake_qa=True,
    )


def product_agent_policy(*, max_bytes_per_file: int = 128_000) -> ToolPermissionPolicy:
    """Default-deny product policy; only ForgeX-owned sandbox writes execute."""

    bounded = max(1, min(int(max_bytes_per_file), 512_000))
    return ToolPermissionPolicy(
        policy_id="forgex-product-agent-v1",
        decisions={
            ToolName.LIST_FILES: "allow",
            ToolName.READ_FILE: "allow",
            ToolName.WRITE_FILE: "allow",
            ToolName.EDIT_FILE_SIMPLE: "deny",
            ToolName.CREATE_REVIEW: "allow_after_validated_diff",
        },
        max_file_bytes=bounded,
        max_content_bytes=bounded,
        automated_fake_qa=True,
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
