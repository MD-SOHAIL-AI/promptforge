"""Read-only source probes; all runtime state is confined to a temporary folder.

Run from repository root with: .venv\Scripts\python.exe docs/review-evidence-2026-10-04/audit_probes.py
These are review evidence, not production changes or a replacement test suite.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.agent_runtime.tool_runtime import _safe_provider_diagnostics
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


class DiagnosticPlanner:
    def to_safe_metadata(self):
        return dict(http_status=200, physical_attempt_id="physical-review",
                    response_headers_received=True, first_event_ms=2311,
                    last_event_ms=2390, stream_event_count=6,
                    timeout_stage="stream_idle", duration_ms=62390,
                    completion_received=False)


async def deadline_probe():
    planner = RouterProductPlanner.__new__(RouterProductPlanner)
    planner._metadata = {}
    planner._deadline = None
    planner.turn_timeout_s = .02
    planner._verification_reserve_s = 0
    planner._recovery_hook = None
    async def delayed_local_work(**kwargs):
        await asyncio.sleep(1)
    planner._request_turn = delayed_local_work
    try:
        await planner._bounded_turn()
    except Exception as error:
        return {"exception_type": type(error).__name__, "error": str(error),
                "metadata": planner._metadata,
                "provider_requests_in_probe": 0}
    raise AssertionError("Expected the local deadline to expire")


async def recovery_probe(root):
    workspace = root / "project"
    workspace.mkdir()
    async def executor(request):
        stage = Path(request.active_workspace_root)
        (stage / "platformio.ini").write_text(
            "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n")
        await request.progress_callback("turn.settled", {"turn": 1,
            "_resume_checkpoint": {"turn": 1, "observations": [], "plan": [],
                                   "tool_calls_used": 0, "elapsed_seconds": 0}})
        return {"success": False, "classification": "PROVIDER_TIMEOUT",
                "message": "Controlled model timeout"}
    owner = AgentOrchestrator(
        change_service=ChangeSetService(state_root=root / "changes", staging_root=root / "stages"),
        provider_registry=_providers(root), store=AgentOrchestrationStore(root / "journal.sqlite3"),
        run_factory=ProductAgentRun, autonomous_executor=executor)
    owner.auto_recovery_enabled = False
    try:
        run = await owner.start_run(project_id="review", active_workspace_root=workspace,
            instruction="build firmware", provider_id="verified_template",
            autonomy="build_only", session_id="review-session")
        await owner._tasks[run.run_id]
        actions = owner.recovery_info(run.run_id)["actions"]
        owner.jobs.mutate(run.job_id, lambda job: job.update(
            recovery_policy="safe_auto", retry_due_at="2020-01-01T00:00:00Z"))
        owner.auto_recovery_enabled = True
        await owner.recover_jobs(run.job_id)
        blocked = owner.jobs.get(run.job_id)
        owner.jobs.trust("review", str(workspace.resolve()), True)
        owner.project_trust_changed("review")
        await asyncio.sleep(.03)
        after = owner.jobs.get(run.job_id)
        return {"actions_before_trust": actions,
                "automatic_recovery_blocker": blocked["blocking_reason"],
                "automatic_recovery_cycles": blocked["auto_recovery_count"],
                "blocker_after_trust": after["blocking_reason"],
                "replacement_started_after_trust": bool(run.recovered_by_run_id),
                "scheduled_retry_tasks_after_trust": len(owner._retry_tasks),
                "note": "Permission-change handler intentionally refreshes eligibility without starting work; safe_auto behavior needs an explicit product contract."}
    finally:
        await owner.close()


async def main():
    os.environ["FORGEX_ENABLE_SHARED_AGENT_ENGINE"] = "1"
    with tempfile.TemporaryDirectory(prefix="forgex-audit-probes-") as folder:
        supplied = DiagnosticPlanner().to_safe_metadata()
        filtered = _safe_provider_diagnostics(DiagnosticPlanner())
        result = {"diagnostic_filter": {"supplied": supplied, "returned": filtered,
                                       "dropped_fields": sorted(set(supplied) - set(filtered))},
                  "local_deadline": await deadline_probe(),
                  "recovery_permissions": await recovery_probe(Path(folder))}
    path = Path(__file__).with_name("probe-results.json")
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
