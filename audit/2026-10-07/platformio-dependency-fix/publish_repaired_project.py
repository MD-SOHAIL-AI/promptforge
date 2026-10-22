"""Publish only the compiler-verified repair under the user's implement request."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.changes.service import ChangeSetService

parser = argparse.ArgumentParser()
parser.add_argument("--verified-attempt", type=int, required=True)
args = parser.parse_args()
audit = Path(__file__).parent
context = json.loads((audit / "repair-context.json").read_text(encoding="utf-8"))
build = json.loads((audit / f"build-{args.verified_attempt}.json").read_text(encoding="utf-8"))
assert build["success"], "Only a successful real build may be published."
service = ChangeSetService(state_root=ROOT / ".promptforge/state/changesets",
                           staging_root=ROOT / ".promptforge/state/change-staging")
original = service.get(context["original_change_set_id"])
baseline = service.inspect_workspace(context["workspace_root"])
assert baseline.tree_hash == context["original_workspace_hash"], "The workspace changed; preserve the user's edits."
updated = service.create_from_stage(provider_id=original.provider_id,
    workspace_root=context["workspace_root"], stage_root=context["repair_stage"],
    baseline=baseline, authorized_paths=("platformio.ini", "src/main.cpp", "README.md"),
    authorization_id="codex-platformio-repair-20261007",
    authorization_source="explicit_user_implementation", risk_level="low", review_required=False)
applied = service.apply(updated.change_set_id)
receipt = applied.to_dict()
receipt["original_change_set_id"] = original.change_set_id
receipt["verified_build_attempt"] = args.verified_attempt
(audit / "applied-repair.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"change_set_id": applied.change_set_id, "status": applied.status,
    "workspace_root": applied.workspace_root, "files": [f.path for f in applied.changed_files]}))
