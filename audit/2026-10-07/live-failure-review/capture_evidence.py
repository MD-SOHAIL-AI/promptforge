"""Capture a bounded, read-only evidence snapshot for the incident review."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT

LOG = ROOT / ".forgex/logs/live-runtime.backend-3b9e2a890609417f91772784a4c4eaf3.jsonl"
events = []
fields = {
    "timestamp", "event", "run_id", "turn_id", "project_id", "model", "provider",
    "status", "status_code", "classification", "error_code", "http_status",
    "finish_reason", "requested_output_tokens", "effective_output_tokens",
    "input_tokens", "output_tokens", "reasoning_tokens", "text_event_count",
    "tool_event_count", "duration_ms", "recovery_sequence", "retryable", "message",
    "first_event_ms", "last_event_ms", "timeout_stage", "worker_exit_code", "path",
}
selected_events = {
    "provider.attempt.failed", "provider.attempt.completed", "provider.request.failed",
    "agent.runtime.paused.model.unavailable", "agent.run.failed",
    "agent.recovery.model.selected", "agent.runtime.interrupted",
    "agent.cancellation.requested", "agent.job.paused", "agent.task.contract.updated",
}
all_events = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()]
for number, event in enumerate(all_events, 1):
    if event.get("event") not in selected_events and not (
        event.get("event") == "http.request.completed" and event.get("status_code", 0) >= 400
    ):
        continue
    entry = {key: value for key, value in event.items() if key in fields}
    if isinstance(entry.get("message"), str):
        entry["message"] = re.sub(r'"user_id"\s*:\s*"[^"]*"', '"user_id":"[removed]"', entry["message"])
    entry["source_line"] = number
    events.append(entry)

database = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
    runs = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload_json FROM agent_runs WHERE project_id=? ORDER BY created_at",
        ("external-esp32-room-monitor-e02997f653",),
    )]
    jobs = [json.loads(row[0]) for row in conn.execute(
        "SELECT payload_json FROM agent_jobs WHERE project_id=?",
        ("external-esp32-room-monitor-e02997f653",),
    )]
run_evidence = []
for run in runs:
    contract = run.get("_recovery_context", {}).get("active_workspace", {}).get("task_contract", {})
    item = {key: run.get(key) for key in (
        "run_id", "job_id", "status", "classification", "provider_id", "model_id",
        "retry_of_run_id", "build_verified", "build_result", "tool_execution_count",
        "created_at", "updated_at", "recovery_actions", "provider_diagnostics", "failure",
    )}
    item["contract"] = {
        "positive_requirement_count": len(contract.get("requirements", [])),
        "exclusion_count": len(contract.get("exclusions", [])),
        "request_characters": len(contract.get("request", "")),
        "request_is_excluded": contract.get("request") in contract.get("exclusions", []),
        "acceptance_checks": contract.get("acceptance_checks"),
        "deliverables": contract.get("deliverables"),
    }
    run_evidence.append(item)

with sqlite3.connect((ROOT / ".promptforge/state/model-router.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as conn:
    settings = json.loads(conn.execute("SELECT payload FROM model_settings").fetchone()[0])
    cached_retired = [item for item in settings["models"]["nvidia_nim"]
        if item.get("model_id") == "qwen/qwen3-coder-480b-a35b-instruct"]

sources = [
    "backend/agent_runtime/task_contract.py", "backend/agent_runtime/product_provider_registry.py",
    "backend/agent_runtime/failure_contract.py", "backend/agent_runtime/jobs.py",
    "backend/model_router/providers/opencode.py", "backend/contracts/transport.py",
    "backend/api/routes/projects.py", "backend/services/project_profile.py",
    "backend/api/routes/models.py", "model-router/src/engine.mjs",
]
result = {
    "review_date": "2026-10-07", "latest_log": str(LOG),
    "latest_log_sha256": hashlib.sha256(LOG.read_bytes()).hexdigest(),
    "latest_log_event_count": len(all_events),
    "latest_log_first_event": all_events[0]["timestamp"],
    "latest_log_last_event": all_events[-1]["timestamp"],
    "current_source_fingerprint": BACKEND_SOURCE_FINGERPRINT,
    "incident_source_fingerprint": all_events[0].get("source_fingerprint"),
    "source_hashes": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
    "events": events, "runs": run_evidence,
    "jobs": [{key: value for key, value in job.items() if key not in {"original_request", "task", "prompt"}} for job in jobs],
    "cached_retired_model": cached_retired,
}
destination = Path(__file__).resolve().parent / "evidence.json"
destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"path": str(destination), "events": len(events), "runs": len(runs),
    "source_matches_incident": BACKEND_SOURCE_FINGERPRINT == result["incident_source_fingerprint"]}))
