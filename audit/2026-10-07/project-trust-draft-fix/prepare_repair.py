"""Preserve the saved draft and prepare an isolated, authorized repair."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.changes.service import ChangeSetService
from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore

audit = Path(__file__).parent
service = ChangeSetService(state_root=ROOT / ".promptforge/state/changesets",
                           staging_root=ROOT / ".promptforge/state/change-staging")
original = service.get("changeset-a3b0e0fee0744ace9370dfc1b77b91c9")
baseline = service.inspect_workspace(original.workspace_root)
assert baseline.tree_hash == original.base_workspace_hash, "Workspace changed; preserve edits."
stage = service.create_stage()
service.materialize_review_draft(original.change_set_id, workspace_root=original.workspace_root, target_stage=stage)
context = {"original_run_id": "agent-run-f5fe78b9980b45d192ee0972f1051599",
           "project_id": "external-new-folder-2-052a85159d",
           "original_change_set_id": original.change_set_id,
           "original_stage": original.staging_root,
           "original_workspace_hash": baseline.tree_hash,
           "workspace_root": original.workspace_root, "repair_stage": str(stage),
           "original_files": {f.path: f.new_hash for f in original.changed_files}}
(audit / "repair-context.json").write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
jobs = AgentJobStore(AgentOrchestrationStore(ROOT / ".promptforge/state/agent-orchestration.sqlite3"))
trust = jobs.trust(context["project_id"], str(Path(original.workspace_root).resolve()), True)
(audit / "project-trust.json").write_text(json.dumps({**trust, "authorization_source": "explicit_user_implementation"}, indent=2) + "\n", encoding="utf-8")
for name in ("verify_firmware.py", "publish_repaired_project.py", "restart_desktop.ps1", "verify_live_runtime.py"):
    shutil.copyfile(audit.parent / "platformio-dependency-fix" / name, audit / name)
print(json.dumps(context, indent=2))
