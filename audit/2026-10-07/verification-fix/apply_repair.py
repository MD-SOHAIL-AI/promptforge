"""Apply the tested, hash-bound firmware ChangeSet to its original project."""
import hashlib
import json
import sys
from pathlib import Path

base = Path(__file__).resolve().parent
repo = base.parents[2]
sys.path.insert(0, str(repo))
from backend.changes.service import ChangeSetService
manifest = json.loads((base / "repair-manifest.json").read_text(encoding="utf-8"))
expected_root = Path(r"C:\Users\diwan\OneDrive\Desktop\simple esp32 project").resolve(strict=True)
owner = ChangeSetService(state_root=repo / ".promptforge/state/changesets", staging_root=repo / ".promptforge/state/change-staging")
change = owner.get(manifest["change_set_id"])
if Path(change.workspace_root).resolve(strict=True) != expected_root:
    raise RuntimeError("Firmware repair target mismatch")
if {item.path: item.new_hash for item in change.changed_files} != manifest["files"]:
    raise RuntimeError("Firmware repair manifest mismatch")
if change.status == "pending":
    change = owner.apply(change.change_set_id)
if change.status != "applied":
    raise RuntimeError("Firmware repair did not apply")
for relative, digest in manifest["files"].items():
    path = (expected_root / relative).resolve(strict=True)
    if not path.is_relative_to(expected_root) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Applied source hash mismatch")
manifest.update(status="applied", applied_at=change.to_dict()["applied_at"])
(base / "repair-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps({"change_set_id": change.change_set_id, "status": change.status,
                  "verified_file_count": len(manifest["files"]), "workspace_root": str(expected_root)}))
