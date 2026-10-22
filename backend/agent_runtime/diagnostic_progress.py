"""Stable failure evidence used to decide whether a firmware repair advanced."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

_VOLATILE = {"build_id", "request_id", "call_id", "timestamp", "created_at",
             "updated_at", "duration_ms", "elapsed_seconds", "cached",
             "build_log_artifact_id", "firmware_artifact_id"}


def stable_value(value):
    if isinstance(value, Mapping):
        return {str(key): stable_value(item) for key, item in value.items()
                if str(key) not in _VOLATILE}
    if isinstance(value, (list, tuple)):
        return [stable_value(item) for item in value]
    return value


def diagnostic_fingerprint(build: Mapping) -> str:
    diagnostics = [*(build.get("compiler_diagnostics") or ()), *(build.get("linker_diagnostics") or ())]
    diagnostics = diagnostics or build.get("structured_diagnostics") or build.get("diagnostics") or []
    if isinstance(diagnostics, (list, tuple)):
        errors = [
            {key: item.get(key) for key in ("path", "severity", "message", "symbol")}
            for item in diagnostics if isinstance(item, Mapping)
            and str(item.get("severity", "error")).lower() not in {"warning", "note"}
        ]
    else:
        errors = []
    if errors:
        value = sorted(errors, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    else:
        text = str(build.get("diagnostics") or build.get("message") or build.get("error_code") or "unknown")
        text = re.sub(r"\b(?:build-[a-f0-9]+|0x[a-f0-9]+|\d+(?:\.\d+)?)\b", "#", text.casefold())
        value = {"error_code": build.get("error_code"), "text": text}
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
