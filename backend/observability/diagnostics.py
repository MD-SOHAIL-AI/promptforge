"""Unified live runtime diagnostics for ForgeX.

The JSONL file is a development aid only. Emission must never break the
runtime path it observes, and values are centrally redacted before writing.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT

DEFAULT_LIVE_LOG_PATH = Path(".forgex") / "logs" / "live-runtime.jsonl"
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_LOCK = threading.RLock()
BACKEND_INSTANCE_ID = uuid.uuid4().hex
_SECRET_KEY_RE = re.compile(r"(authorization|cookie|api[_-]?key|token|password|secret|credential|private[_-]?key)", re.I)
_TOKEN_COUNT_KEYS = frozenset({"input_tokens", "output_tokens", "total_tokens", "output_tokens_limit", "max_tokens", "tokens_used", "prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "requested_output_tokens", "effective_output_tokens"})
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I)
_COMMON_SECRET_RE = re.compile(r"\b(?:sk|sk-or|ghp|AIza)[A-Za-z0-9_\-]{12,}\b")
_ALLOWED_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def live_log_path() -> Path:
    configured = os.getenv("FORGEX_LIVE_LOG_PATH", "").strip()
    base = Path(configured) if configured else DEFAULT_LIVE_LOG_PATH
    return base.with_name(f"{base.stem}.backend-{BACKEND_INSTANCE_ID}{base.suffix}")


def emit_diagnostic_event(
    *,
    component: str,
    event: str,
    level: str = "INFO",
    message: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Write one safe diagnostic event and return the serialized payload.

    Malformed or non-JSON-compatible field values are represented safely. File
    write and rotation failures are intentionally swallowed.
    """

    payload = _event_payload(component=component, event=event, level=level, message=message, fields=fields)
    try:
        with _LOCK:
            path = live_log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed(path, _max_bytes())
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
    except Exception:
        pass
    try:
        _print_condensed(payload)
    except Exception:
        # Console output is optional diagnostics. A closed pipe or an unusual
        # stream implementation must never change the result of an agent turn.
        pass
    return payload


def redact(value: Any) -> Any:
    return _redact(value, depth=0)


def _event_payload(*, component: str, event: str, level: str, message: str | None, fields: Mapping[str, Any]) -> dict[str, Any]:
    normalized_level = level.strip().upper() if isinstance(level, str) else "INFO"
    if normalized_level == "WARN":
        normalized_level = "WARNING"
    if normalized_level not in _ALLOWED_LEVELS:
        normalized_level = "INFO"
    payload: dict[str, Any] = {
        "backend_instance_id": BACKEND_INSTANCE_ID,
        "desktop_launch_id": os.getenv("FORGEX_DESKTOP_LAUNCH_ID"),
        "pid": os.getpid(),
        "app_version": os.getenv("FORGEX_VERSION", "development"),
        "source_fingerprint": BACKEND_SOURCE_FINGERPRINT,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "level": normalized_level,
        "component": _short_text(component or "runtime", 128),
        "event": _short_text(event or "runtime.event", 160),
    }
    if message:
        payload["message"] = _sanitize_string(str(message), 2000)
    for key, value in fields.items():
        if not isinstance(key, str) or not key or key in {"timestamp", "backend_instance_id", "desktop_launch_id", "pid", "app_version", "source_fingerprint"}:
            continue
        safe = "[redacted]" if _secret_field(key, value) else redact(value)
        if safe is not None:
            payload[key] = safe
    return payload


def _secret_field(key: str, value: Any) -> bool:
    if key in _TOKEN_COUNT_KEYS and type(value) is int and value >= 0:
        return False
    return bool(_SECRET_KEY_RE.search(key))


def _redact(value: Any, *, depth: int) -> Any:
    if depth > 8:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else None
    if isinstance(value, str):
        return _sanitize_string(value, 16_000)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                continue
            if _secret_field(key, item):
                out[key] = "[redacted]"
            else:
                out[key] = _redact(item, depth=depth + 1)
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, BaseException):
        return {
            "name": type(value).__name__,
            "message": _sanitize_string(str(value), 2000),
        }
    return _sanitize_string(repr(value), 1000)


def _sanitize_string(value: str, limit: int) -> str:
    text = value.replace("\x00", "")
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _COMMON_SECRET_RE.sub("[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|wifi[_-]?password|password|auth[_-]?token|secret)\s*[=:]\s*[\"']?)[^\s\"';,]+", r"\1[redacted]", text)
    if len(text) > limit:
        return text[:limit] + "\n[truncated]"
    return text


def _short_text(value: str, limit: int) -> str:
    cleaned = " ".join(str(value).replace("\x00", "").split())
    return cleaned[:limit] or "unknown"


def _max_bytes() -> int:
    raw = os.getenv("FORGEX_LIVE_LOG_MAX_BYTES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES
    return max(64 * 1024, min(value, 100 * 1024 * 1024))


def _rotate_if_needed(path: Path, max_bytes: int) -> None:
    try:
        if not path.exists() or path.stat().st_size < max_bytes:
            return
        rotated = path.with_name(path.name + ".1")
        if rotated.exists():
            rotated.unlink()
        path.replace(rotated)
    except OSError:
        pass


def _print_condensed(payload: Mapping[str, Any]) -> None:
    event = str(payload.get("event") or "")
    if str(payload.get("level")) == "DEBUG" or event.startswith("http.request"):
        return
    component = str(payload.get("component") or "runtime")
    message = str(payload.get("message") or payload.get("status") or "")
    line: str | None = None
    if event.startswith("provider.request."):
        line = f"[provider] {payload.get('provider', '?')} -> {payload.get('model', '?')} {event.rsplit('.', 1)[-1]} {payload.get('status', '')} {payload.get('duration_ms', '')}ms"
    elif event.startswith("tool."):
        line = f"[tool] {payload.get('tool', payload.get('name', '?'))} {event.rsplit('.', 1)[-1]} {payload.get('duration_ms', '')}ms"
    elif event.startswith("build."):
        line = f"[build] {event.rsplit('.', 1)[-1].upper()} {message[:160]}"
    elif event.startswith("agent."):
        line = f"[agent] {event.removeprefix('agent.')} {str(payload.get('run_id') or '')[:12]} {message[:160]}"
    elif component in {"desktop", "renderer"} or event.startswith(("sse.", "websocket.")):
        line = f"[{component}] {event} {message[:160]}"
    if line is not None:
        # Windows terminals may use cp1252. Keep the complete Unicode payload
        # in JSONL and make this human-readable mirror safe for any code page.
        print(line.strip().encode("ascii", "backslashreplace").decode("ascii"), flush=True)
