"""Bounded, tolerant merging of per-instance and legacy runtime diagnostics."""
import json
import os
from pathlib import Path

from .diagnostics import DEFAULT_LIVE_LOG_PATH, redact


def log_sources(base=None):
    base = Path(base or os.getenv("FORGEX_LIVE_LOG_PATH") or DEFAULT_LIVE_LOG_PATH)
    candidates = [base, base.with_name(base.name + ".1")]
    candidates.extend(base.parent.glob(base.stem + ".backend-*" + base.suffix + "*"))
    candidates.extend(base.parent.glob(base.stem + ".desktop-*" + base.suffix + "*"))
    inspected = []
    for path in set(candidates):
        try:
            if path.is_file():
                inspected.append((path.stat().st_mtime, path))
        except OSError:
            # Another instance may rotate its file between discovery and stat.
            continue
    return [path for _, path in sorted(inspected, key=lambda item: item[0], reverse=True)[:128]]


def read_run_logs(run_id, *, base=None, limit=2000):
    events, malformed = [], 0
    allowed = {"timestamp", "level", "component", "event", "event_type", "run_id", "status", "classification",
               "backend_instance_id", "desktop_launch_id", "pid", "app_version", "source_fingerprint",
               "call_id", "tool", "duration_ms", "http_status", "retry_after_ms", "request_id", "provider_id", "model_id"}
    sources = log_sources(base)
    for path in sources:
        try:
            with path.open("rb") as stream:
                if path.stat().st_size > 2 * 1024 * 1024:
                    stream.seek(-2 * 1024 * 1024, 2)
                    stream.readline()
                for raw in stream:
                    if len(raw) > 256 * 1024:
                        malformed += 1
                        continue
                    try:
                        event = json.loads(raw)
                        if not isinstance(event, dict):
                            raise ValueError("Non-object record")
                    except (ValueError, UnicodeError):
                        malformed += 1
                        continue
                    if event.get("run_id") == run_id:
                        events.append(redact({key: value for key, value in event.items() if key in allowed}))
        except OSError:
            continue
    events.sort(key=lambda event: str(event.get("timestamp", "")))
    return {"events": events[-limit:], "source_count": len(sources), "malformed_records_skipped": malformed,
            "bounded": True}
