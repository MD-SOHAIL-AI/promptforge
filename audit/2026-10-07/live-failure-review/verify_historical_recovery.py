"""Preview requirement recovery against the incident's real saved contracts.

The production database is opened read-only. Only this audit output is written.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(prefix="forgex-recovery-audit-") as isolated:
    os.environ["FORGEX_ROOT"] = isolated
    os.environ["FORGEX_SETTINGS_PATH"] = str(Path(isolated) / "settings.json")
    os.environ["FORGEX_MODEL_ROUTER_SETTINGS_PATH"] = str(Path(isolated) / "models.json")
    os.environ["FORGEX_LIVE_LOG_PATH"] = str(Path(isolated) / "live.jsonl")
    from backend.agent_runtime.task_contract import repair_task_contract
    from backend.agent_runtime.failure_contract import failure_details
    with sqlite3.connect((ROOT / ".promptforge/state/agent-orchestration.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        runs = [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM agent_runs WHERE project_id=? ORDER BY created_at",
            ("external-esp32-room-monitor-e02997f653",))]
    preview = []
    for run in runs:
        context = run.get("_recovery_context") or {}
        contract = (context.get("active_workspace") or {}).get("task_contract")
        if not contract:
            continue
        repaired = repair_task_contract(contract, workspace=Path(context["workspace_root"]))
        assert len(repaired["requirements"]) > 0
        assert any(item["kind"] == "build" for item in repaired["acceptance_checks"])
        assert any("do not flash" in item.casefold() for item in repaired["exclusions"])
        preview.append({"run_id": run["run_id"], "before_requirements": len(contract["requirements"]),
            "after_requirements": len(repaired["requirements"]),
            "acceptance_checks": [item["kind"] for item in repaired["acceptance_checks"]],
            "deliverables": [item["kind"] for item in repaired["deliverables"]],
            "historical_revision_retained": contract["revision"] in repaired["revision_history"]})
    assert preview
    baseline = failure_details("RATE_LIMITED", status="failed")
    assert baseline["category"] == "budget_exhaustion" and baseline["retryable"] is False
    result = {"saved_run_contracts": preview, "api_rate_limit_behavior_unchanged": True,
              "production_database_mode": "read-only", "production_runs_resumed": False}
    Path(__file__).with_name("recovery-validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
