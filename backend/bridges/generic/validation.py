"""Validation and sanitization shared by generic bridge contracts."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .errors import BridgeDomainError, BridgeErrorCode


ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{2,127}$")
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
KNOWN_PROVIDER_IDS = frozenset(
    {
        "agy",
        "codex",
        "antigravity_cli_bridge",
        "codex_bridge",
        "claude_code_bridge",
        "opencode_bridge",
    }
)
# Canonical local-agent IDs. Registration and feature policy still default-deny
# execution independently of this validation allowlist.
IMPLEMENTED_GENERIC_PROVIDER_IDS: frozenset[str] = frozenset({"agy", "codex"})
MAX_SAFE_MESSAGE_LENGTH = 512
MIN_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 900

_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\s\"']+")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^\s/]+/)+[^\s\"']*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|token|password|cookie|credential|secret)\b\s*[:=]\s*[^\s,;]+"
)
_PATCH_MARKERS = ("diff --git", "@@ ", "--- a/", "+++ b/")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BridgeDomainError(
            BridgeErrorCode.INVALID_REQUEST,
            f"{field_name} must include a timezone.",
        )
    return value.astimezone(timezone.utc)


def format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeDomainError(BridgeErrorCode.RECORD_CORRUPT) from exc
    return ensure_utc(parsed, field_name)


def validate_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise BridgeDomainError(
            BridgeErrorCode.INVALID_REQUEST,
            f"{field_name} is invalid.",
        )
    return value


def validate_provider_id(value: str, *, allowed: frozenset[str] = KNOWN_PROVIDER_IDS) -> str:
    if not isinstance(value, str) or not PROVIDER_ID_PATTERN.fullmatch(value) or value not in allowed:
        raise BridgeDomainError(BridgeErrorCode.PROVIDER_UNSUPPORTED)
    return value


def validate_safe_code(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not SAFE_CODE_PATTERN.fullmatch(value):
        raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, f"{field_name} is invalid.")
    return value


def sanitize_message(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value:
        raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Safe message is invalid.")
    lowered = value.casefold()
    if any(marker in lowered for marker in _PATCH_MARKERS):
        raise BridgeDomainError(
            BridgeErrorCode.INVALID_REQUEST,
            "Patch content is not permitted in bridge events.",
        )
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    text = _WINDOWS_PATH.sub("[PATH]", text)
    text = _POSIX_PATH.sub("[PATH]", text)
    text = " ".join(text.split())
    if len(text) > MAX_SAFE_MESSAGE_LENGTH:
        text = text[: MAX_SAFE_MESSAGE_LENGTH - 1].rstrip() + "…"
    return text
