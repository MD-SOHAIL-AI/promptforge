"""Read provider configuration and recorded physical-attempt timings safely."""
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[3]
database = ROOT / ".promptforge/state/model-router.sqlite3"
configured = []
state = {}
if database.exists():
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload FROM model_settings LIMIT 1").fetchone()
    state = json.loads(row[0]) if row else {}
legacy_settings = ROOT / ".promptforge/state/model-router.json"
if not state.get("providers") and legacy_settings.exists():
    state = json.loads(legacy_settings.read_text(encoding="utf-8"))
for identity, value in state.get("providers", {}).items():
    configured.append({"provider": identity, **{key: value[key] for key in
        ("enabled", "default_model") if key in value}})
records = {}
for log in (ROOT / ".forgex/logs").glob("live-runtime.backend-*.jsonl"):
    with log.open(encoding="utf-8") as lines:
        for index, line in enumerate(lines):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if event.get("event") not in {"provider.attempt.completed", "provider.attempt.failed"}:
                continue
            if not event.get("provider") or not event.get("model"):
                continue
            identity = event.get("physical_attempt_id") or f"{log.name}:{index}"
            records[identity] = {key: event.get(key) for key in ("provider", "model", "duration_ms",
                "first_event_ms", "last_event_ms", "completion_received", "error_code", "timeout_stage")}
groups = defaultdict(list)
for event in records.values():
    groups[(event["provider"], event["model"])].append(event)

def percentile(values, fraction):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] / 1000, 3) if ordered else None

summary = []
for (provider, model), attempts in sorted(groups.items()):
    succeeded = [item for item in attempts if not item["error_code"] and item["completion_received"]]
    durations = [item["duration_ms"] for item in succeeded if isinstance(item["duration_ms"], (int, float))]
    first = [item["first_event_ms"] for item in attempts if isinstance(item["first_event_ms"], (int, float))]
    summary.append({"provider": provider, "model": model, "attempts": len(attempts),
        "completed_successfully": len(succeeded), "first_event_samples": len(first),
        "first_event_seconds": {"p50": percentile(first, .5), "p95": percentile(first, .95), "max": percentile(first, 1)},
        "successful_completion_seconds": {"p50": percentile(durations, .5), "p95": percentile(durations, .95), "max": percentile(durations, 1)},
        "successful_completion_observed_fraction": {str(seconds): round(sum(value <= seconds * 1000 for value in durations) / len(durations), 3)
            for seconds in (5, 15, 30, 60, 120, 180, 300)} if durations else {},
        "errors": dict(Counter(item["error_code"] for item in attempts if item["error_code"])),
        "timeout_stages": dict(Counter(item["timeout_stage"] for item in attempts if item["timeout_stage"]))})
result = {"providers_in_workspace_settings": configured, "recorded_physical_attempts": len(records), "timings": summary,
    "configuration_note": "Workspace settings only; historical desktop attempts may use a separate application settings path.",
    "measurement_limitations": ["Historical local samples, not provider guarantees.",
        "Completion percentiles/fractions include successful complete attempts only; failed/time-limited attempts are separate.",
        "First events may be text, reasoning, tools or completion; these are not pure first-visible-text measurements."]}
Path(__file__).with_name("observed-timings.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, indent=2))
