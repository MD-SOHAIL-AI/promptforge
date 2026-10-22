"""Refresh this authorized project's permission projection without replaying work."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore

audit = Path(__file__).parent
context = json.loads((audit / "repair-context.json").read_text(encoding="utf-8"))
jobs = AgentJobStore(AgentOrchestrationStore(ROOT / ".promptforge/state/agent-orchestration.sqlite3"))
job = jobs.get("job-85678050efb2474fb489a56a64d20506")
assert job["project_id"] == context["project_id"]
assert job["current_run_id"] == context["original_run_id"]
assert jobs.is_trusted(job["project_id"], job["workspace_root"])
if job["blocking_reason"] == "AGENT_PROJECT_TRUST_REQUIRED":
    refreshed = jobs.mutate(job["job_id"], lambda value: value.update(blocking_reason=None))
    for field in ("consumed", "limits", "current_run_id", "status"):
        assert refreshed[field] == job[field]
(audit / "permission-projection.json").write_text(json.dumps({"job_id": job["job_id"],
    "trusted": True, "budget_preserved": True, "historical_run_preserved": True,
    "work_replayed": False}, indent=2) + "\n", encoding="utf-8")
print("Project permission refreshed; saved run history and budget preserved.")
