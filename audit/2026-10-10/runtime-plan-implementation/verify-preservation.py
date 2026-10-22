"""Read-only comparison of generated files against the pre-implementation snapshot."""
import hashlib
import json
from pathlib import Path

directory = Path(__file__).resolve().parent
before = json.loads((directory / "generated-files-before.json").read_text(encoding="utf-8"))
root = directory.parents[2]
roots = [root / ".promptforge/state/change-staging", root / "workspace/projects", Path("D:/projects/New folder")]
after = {}
for source in roots:
    if source.exists():
        for file in source.rglob("*"):
            if file.is_file() and not file.is_symlink():
                after[str(file.absolute())] = hashlib.sha256(file.read_bytes()).hexdigest()
result = {"checked_files": len(before), "current_files": len(after),
    "modified": sorted(path for path in before.keys() & after.keys() if before[path] != after[path]),
    "removed": sorted(before.keys() - after.keys()), "added": sorted(after.keys() - before.keys())}
result["unchanged"] = not any(result[key] for key in ("modified", "removed", "added"))
(directory / "generated-file-preservation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result))
raise SystemExit(0 if result["unchanged"] else 1)
