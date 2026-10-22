"""Export selected diagnostic fields from existing journals in SQLite read-only mode."""
import json
import sqlite3
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
RUN_ID = "agent-run-5fb8fa53ab0c40fe8ec1097e994637c7"
db_path = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
    run = json.loads(db.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (RUN_ID,)).fetchone()[0])
    job = json.loads(db.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (run["job_id"],)).fetchone()[0])
    snapshot = {key: run.get(key) for key in (
        "run_id", "job_id", "created_at", "updated_at", "status", "classification",
        "provider_id", "model_id", "build_verified", "provider_diagnostics")}
    snapshot["job"] = {key: job.get(key) for key in (
        "status", "recovery_policy", "paused_by", "blocking_reason", "auto_recovery_count",
        "limits", "consumed", "outage", "retry_due_at")}
    snapshot["deliverables"] = [
        {key: item.get(key) for key in ("id", "kind", "path", "status")}
        for item in (run.get("task_state") or {}).get("deliverable_manifest", ())
    ]
    snapshot["recovery_events"] = []
    for payload, in db.execute("SELECT payload_json FROM agent_events WHERE run_id=? AND event_type IN ('job.recovery.blocked','job.permissions.changed','runtime.paused.model.unavailable') ORDER BY sequence", (RUN_ID,)):
        event = json.loads(payload)
        snapshot["recovery_events"].append({key: event.get(key) for key in (
            "event_type", "sequence", "created_at", "blocking_reason", "classification")})
    snapshot["historical_status_counts"] = dict(db.execute("SELECT status,count(*) FROM agent_runs GROUP BY status"))
snapshot["transport_events"] = []
allowed = {"event", "timestamp", "created_at", "time", "physical_attempt_id", "run_id",
           "turn", "http_status", "response_headers_received", "first_event_ms", "last_event_ms",
           "stream_event_count", "completion_received", "timeout_stage", "duration_ms",
           "provider_id", "model_id", "request_reached_provider"}
def filtered(value):
    if not isinstance(value, dict):
        return {}
    result = {key: item for key, item in value.items() if key in allowed and not isinstance(item, (dict, list))}
    for item in value.values():
        if isinstance(item, dict):
            result.update(filtered(item))
    return result
for path in sorted((ROOT / ".forgex/logs").glob("*.jsonl")):
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if "3e3bbbef0d9c4cbdb1ec7ead53154509" not in line:
                continue
            try:
                event = filtered(json.loads(line))
            except ValueError:
                continue
            if event:
                snapshot["transport_events"].append(event)
(OUT / "latest-failure.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
inventory = {}
suffixes = {".py", ".ts", ".tsx", ".mts", ".mjs", ".cjs", ".css"}
for directory in ("backend", "frontend", "electron", "harness", "scripts", "tests"):
    files = subprocess.check_output(["rg", "--files", directory], cwd=ROOT, text=True).splitlines()
    selected = [ROOT / name for name in files if Path(name).suffix in suffixes and "vendor" not in Path(name).parts and "public" not in Path(name).parts]
    inventory[directory] = {"files": len(selected), "lines": sum(len(path.read_text(encoding="utf-8", errors="replace").splitlines()) for path in selected)}
(OUT / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"inventory": inventory, "latest_run": snapshot["run_id"], "recovery_events": snapshot["recovery_events"], "transport_events": snapshot["transport_events"]}, indent=2))
