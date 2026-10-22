"""Read-only status and evidence for the UI-submitted qualification run."""
import json
import sqlite3
from pathlib import Path

case = Path(__file__).resolve().parent
database = case / "data/.promptforge/state/agent-orchestration.sqlite3"
with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
    row = connection.execute("SELECT run_id,payload_json FROM agent_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        print("No run admitted yet.")
    else:
        run = json.loads(row[1])
        (case / "run-current.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        fields = {key: run.get(key) for key in ("run_id", "session_id", "status", "classification", "model_id",
            "tool_execution_count", "last_turn", "change_set_id", "build_verified", "job_id", "assistant_message")}
        fields["latest_events"] = [json.loads(item[0]) for item in connection.execute(
            "SELECT payload_json FROM agent_events WHERE run_id=? ORDER BY sequence DESC LIMIT 4", (row[0],))]
        fields["latest_events"] = [{key: event.get(key) for key in ("event_type", "turn", "tool", "status", "message", "classification", "duration_ms")}
            for event in fields["latest_events"]]
        job = connection.execute("SELECT payload_json FROM agent_jobs WHERE job_id=?", (run.get("job_id"),)).fetchone()
        if job:
            job = json.loads(job[0]); fields["consumed"] = job.get("consumed"); fields["limits"] = job.get("limits")
        logs = sorted(case.glob('live-runtime.backend-*.jsonl'), key=lambda path: path.stat().st_mtime)
        if logs:
            records = [json.loads(line) for line in logs[-1].read_text(encoding='utf-8').splitlines()]
            fields['worker_failures'] = [{key: record.get(key) for key in ('event', 'error_code', 'worker_exit_code',
                'worker_cleanup_incomplete', 'duration_ms', 'timeout_stage')} for record in records
                if record.get('event') == 'provider.attempt.failed'][-3:]
        print(json.dumps(fields, indent=2))
