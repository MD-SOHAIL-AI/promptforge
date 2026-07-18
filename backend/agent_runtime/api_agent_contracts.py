"""Strict, versioned contracts for API coding-agent proposals.

This module only parses untrusted provider output.  It does not write files or
execute command suggestions.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping


API_CODING_AGENT_SCHEMA_VERSION = "forgex.api_coding_agent.v1"

MAX_FILES = 20
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_SUMMARY_CHARS = 2_000
MAX_COMMAND_SUGGESTIONS = 10
MAX_RISKS = 20
MAX_NEXT_STEPS = 20

SUPPORTED_FILE_ACTIONS = frozenset({"create_or_update", "delete"})

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "summary",
    "files",
    "commands_suggested",
    "risks",
    "next_steps",
}
_PROTECTED_PARTS = frozenset({
    ".git",
    ".pio",
    ".promptforge",
    ".forgex",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
})
_SENSITIVE_NAMES = frozenset({".env", ".env.local", "id_rsa", "id_ed25519"})
_SENSITIVE_SUFFIXES = (".env", ".pem", ".key", ".p12", ".pfx")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul", "clock$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})
_BINARY_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ApiCodingAgentContractError(ValueError):
    """Raised when a coding-agent response fails contract validation."""

    def __init__(self, errors: str | list[str] | tuple[str, ...]) -> None:
        if isinstance(errors, str):
            errors = (errors,)
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class ApiCodingAgentFileOperation:
    path: str
    action: str
    content: str | None = None


@dataclass(frozen=True, slots=True)
class ApiCodingAgentCommandSuggestion:
    """Advisory text only; no execution behavior is attached to this type."""

    command: str
    reason: str


@dataclass(frozen=True, slots=True)
class ApiCodingAgentProposal:
    schema_version: str
    summary: str
    files: tuple[ApiCodingAgentFileOperation, ...]
    commands_suggested: tuple[ApiCodingAgentCommandSuggestion, ...]
    risks: tuple[str, ...]
    next_steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApiCodingAgentContext:
    project_name: str | None = None
    file_tree: tuple[str, ...] = ()
    selected_files: Mapping[str, str] = field(default_factory=dict)


def parse_api_coding_agent_response(
    raw: str,
    *,
    allow_delete: bool = False,
    allow_empty: bool = False,
) -> ApiCodingAgentProposal:
    """Parse and validate one unwrapped JSON coding-agent proposal."""

    if not isinstance(raw, str):
        raise ApiCodingAgentContractError("response must be a JSON string")
    try:
        decoded: Any = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ApiCodingAgentContractError("response is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ApiCodingAgentContractError("response must be a JSON object")

    errors: list[str] = []
    missing = _TOP_LEVEL_FIELDS - set(decoded)
    unknown = set(decoded) - _TOP_LEVEL_FIELDS
    errors.extend(f"missing required field: {name}" for name in sorted(missing))
    if unknown:
        errors.append(f"unknown top-level fields: {', '.join(sorted(unknown))}")

    schema_version = decoded.get("schema_version")
    if "schema_version" in decoded:
        if not isinstance(schema_version, str):
            errors.append("schema_version must be a string")
        elif schema_version != API_CODING_AGENT_SCHEMA_VERSION:
            errors.append(f"unsupported schema_version: {schema_version}")

    summary = decoded.get("summary")
    if "summary" in decoded:
        if not isinstance(summary, str) or not summary.strip():
            errors.append("summary must be a non-empty string")
        elif len(summary) > MAX_SUMMARY_CHARS:
            errors.append(f"summary exceeds max_summary_chars ({MAX_SUMMARY_CHARS})")

    files = _parse_files(
        decoded.get("files"),
        present="files" in decoded,
        allow_delete=allow_delete,
        allow_empty=allow_empty,
        errors=errors,
    )
    commands = _parse_commands(
        decoded.get("commands_suggested"),
        present="commands_suggested" in decoded,
        errors=errors,
    )
    risks = _parse_string_list(
        decoded.get("risks"), "risks", MAX_RISKS,
        present="risks" in decoded, errors=errors,
    )
    next_steps = _parse_string_list(
        decoded.get("next_steps"), "next_steps", MAX_NEXT_STEPS,
        present="next_steps" in decoded, errors=errors,
    )

    if errors:
        raise ApiCodingAgentContractError(errors)
    return ApiCodingAgentProposal(
        schema_version=API_CODING_AGENT_SCHEMA_VERSION,
        summary=summary,
        files=tuple(files),
        commands_suggested=tuple(commands),
        risks=tuple(risks),
        next_steps=tuple(next_steps),
    )


def _parse_files(
    value: object,
    *,
    present: bool,
    allow_delete: bool,
    allow_empty: bool,
    errors: list[str],
) -> list[ApiCodingAgentFileOperation]:
    if not present:
        return []
    if not isinstance(value, list):
        errors.append("files must be a list")
        return []
    if not value and not allow_empty:
        errors.append("files must contain at least one operation")
    if len(value) > MAX_FILES:
        errors.append(f"files exceeds max_files ({MAX_FILES})")

    parsed: list[ApiCodingAgentFileOperation] = []
    seen_paths: set[str] = set()
    total_bytes = 0
    for index, item in enumerate(value[: MAX_FILES + 1]):
        prefix = f"files[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        unknown = set(item) - {"path", "action", "content"}
        if unknown:
            errors.append(f"{prefix} has unknown fields: {', '.join(sorted(unknown))}")

        path = item.get("path")
        if "path" not in item:
            errors.append(f"{prefix}.path is required")
        elif not isinstance(path, str) or not path:
            errors.append(f"{prefix}.path must be a non-empty string")
        else:
            path_error = _path_error(path)
            if path_error:
                errors.append(f"{prefix}.path {path_error}")
            normalized = path.casefold()
            if normalized in seen_paths:
                errors.append(f"{prefix}.path duplicates another file path")
            seen_paths.add(normalized)

        action = item.get("action")
        if "action" not in item:
            errors.append(f"{prefix}.action is required")
        elif not isinstance(action, str) or action not in SUPPORTED_FILE_ACTIONS:
            errors.append(f"{prefix}.action is unsupported")

        content = item.get("content")
        if action == "create_or_update":
            if "content" not in item:
                errors.append(f"{prefix}.content is required for create_or_update")
            elif not isinstance(content, str):
                errors.append(f"{prefix}.content must be text")
            else:
                try:
                    content_bytes = len(content.encode("utf-8"))
                except UnicodeEncodeError:
                    errors.append(f"{prefix}.content must be valid UTF-8 text")
                else:
                    total_bytes += content_bytes
                    if content_bytes > MAX_FILE_BYTES:
                        errors.append(f"{prefix}.content exceeds max_file_bytes ({MAX_FILE_BYTES})")
                    if _looks_binary(content):
                        errors.append(f"{prefix}.content appears to be binary")
        elif action == "delete":
            if not allow_delete:
                errors.append(f"{prefix}.action delete is disabled")
            if content is not None:
                errors.append(f"{prefix}.content must be omitted for delete")

        if isinstance(path, str) and path and isinstance(action, str):
            parsed.append(ApiCodingAgentFileOperation(path=path, action=action, content=content if isinstance(content, str) else None))

    if total_bytes > MAX_TOTAL_BYTES:
        errors.append(f"file content exceeds max_total_bytes ({MAX_TOTAL_BYTES})")
    return parsed


def _parse_commands(value: object, *, present: bool, errors: list[str]) -> list[ApiCodingAgentCommandSuggestion]:
    if not present:
        return []
    if not isinstance(value, list):
        errors.append("commands_suggested must be a list")
        return []
    if len(value) > MAX_COMMAND_SUGGESTIONS:
        errors.append(f"commands_suggested exceeds limit ({MAX_COMMAND_SUGGESTIONS})")
    parsed: list[ApiCodingAgentCommandSuggestion] = []
    for index, item in enumerate(value[: MAX_COMMAND_SUGGESTIONS + 1]):
        prefix = f"commands_suggested[{index}]"
        if not isinstance(item, dict) or set(item) != {"command", "reason"}:
            errors.append(f"{prefix} must contain only command and reason")
            continue
        command, reason = item["command"], item["reason"]
        if not isinstance(command, str) or not command.strip():
            errors.append(f"{prefix}.command must be a non-empty string")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{prefix}.reason must be a non-empty string")
        if isinstance(command, str) and command.strip() and isinstance(reason, str) and reason.strip():
            parsed.append(ApiCodingAgentCommandSuggestion(command=command, reason=reason))
    return parsed


def _parse_string_list(
    value: object,
    name: str,
    limit: int,
    *,
    present: bool,
    errors: list[str],
) -> list[str]:
    if not present:
        return []
    if not isinstance(value, list):
        errors.append(f"{name} must be a list")
        return []
    if len(value) > limit:
        errors.append(f"{name} exceeds limit ({limit})")
    parsed: list[str] = []
    for index, item in enumerate(value[: limit + 1]):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{name}[{index}] must be a non-empty string")
        else:
            parsed.append(item)
    return parsed


def _path_error(path: str) -> str | None:
    if "\x00" in path:
        return "contains a NUL byte"
    if not path.isascii() or unicodedata.normalize("NFKC", path) != path:
        return "contains unsupported Unicode characters"
    if "\\" in path:
        return "must use relative forward-slash syntax"
    windows = PureWindowsPath(path)
    if path.startswith("/") or PurePosixPath(path).is_absolute() or windows.is_absolute() or windows.drive:
        return "must be relative"
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return "contains an unsafe path segment"
    for part in parts:
        folded = part.casefold()
        stem = folded.split(".", 1)[0]
        if folded in _PROTECTED_PARTS:
            return "targets a protected path"
        if folded in _SENSITIVE_NAMES or folded.endswith(_SENSITIVE_SUFFIXES):
            return "targets a sensitive file"
        if stem in _WINDOWS_RESERVED_NAMES or ":" in part or part.endswith((" ", ".")):
            return "contains an unsafe path segment"
    return None


def _looks_binary(content: str) -> bool:
    if not content:
        return False
    controls = _BINARY_CONTROL_RE.findall(content)
    return bool(controls) and ("\x00" in content or len(controls) / len(content) > 0.01)
