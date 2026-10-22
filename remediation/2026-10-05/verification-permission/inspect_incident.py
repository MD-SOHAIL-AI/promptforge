"""Read-only, bounded evidence for the latest verification permission incident."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
RUN = "agent-run-2f0412b3c7b04e69a020a072b0514f67"
db_path = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
try:
    payload = json.loads(db.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (RUN,)).fetchone()[0])
    job = json.loads(db.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (payload["job_id"],)).fetchone()[0])
    row = db.execute("SELECT payload_json FROM agent_project_trust WHERE project_id=?", (payload["project_id"],)).fetchone()
    trust = json.loads(row[0]) if row else {}
    keys = ("run_id", "project_id", "status", "classification", "build_result", "build_verified",
        "change_set_id", "recoverable", "recovery", "stage_statuses", "stage_messages", "tool_execution_count")
    evidence = {key: payload.get(key) for key in keys}
    evidence["trust_record_exists"] = bool(row)
    evidence["trusted_for_original_workspace"] = trust.get("trusted") is True and trust.get("workspace_root") == job["workspace_root"]
    evidence["job"] = {key: job.get(key) for key in ("status", "blocking_reason", "paused_by", "limits", "consumed")}
finally:
    db.close()
latest = max((ROOT / ".forgex/logs").glob("live-runtime.backend-*.jsonl"), key=lambda path: path.stat().st_mtime)
events = []
for number, line in enumerate(latest.read_text(encoding="utf-8").splitlines(), 1):
    row = json.loads(line)
    if row.get("run_id") == RUN and (row.get("event", "").endswith("failed") or row.get("tool") == "build_firmware"):
        events.append({"line": number, **{key: row.get(key) for key in
            ("timestamp", "event", "tool", "classification", "message")}})
evidence.update(log=str(latest.relative_to(ROOT)), events=events)
(OUT / "incident.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
print(json.dumps(evidence, indent=2))
