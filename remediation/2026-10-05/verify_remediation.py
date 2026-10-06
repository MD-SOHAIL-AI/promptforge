"""Record checked-in audit deltas and de-duplicated verification evidence."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
baseline = json.loads((ROOT / "audit/2026-10-05/source-hashes-before.json").read_text())
inventory = json.loads((ROOT / "audit/2026-10-05/inventory.json").read_text())
source_paths = set(baseline)
source_paths.update(subprocess.check_output([
    "rg", "--files", "backend", "tests", "scripts", "electron", "harness", "model-router",
    "-g", "*.py", "-g", "*.ts", "-g", "*.mjs",
], cwd=ROOT, text=True).splitlines())
source_paths = {name.replace("\\", "/") for name in source_paths}
changed = [name for name, checksum in baseline.items()
           if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum]
added = sorted(source_paths - set(baseline))
syntax_errors = []
compiled = 0
for name in sorted(source_paths):
    if not name.endswith(".py"):
        continue
    try:
        compile((ROOT / name).read_bytes(), name, "exec")
        compiled += 1
    except (OSError, SyntaxError) as error:
        syntax_errors.append({"path": name, "error": str(error)})

# Later evidence supersedes the same test's earlier outcome. This avoids
# adding overlapping targeted reruns to the full-suite test count.
tests = {}
evidence = ["python-unit.xml", "python-other-final.xml",
            "regressions-complete.xml", "provider-final.xml", "context-final.xml"]
for name in evidence:
    for test in ET.parse(HERE / "logs" / name).iter("testcase"):
        result = ("failed" if test.find("failure") is not None else
                  "error" if test.find("error") is not None else
                  "skipped" if test.find("skipped") is not None else "passed")
        tests[(test.attrib.get("classname", ""), test.attrib["name"])] = {
            "result": result, "evidence": name,
        }
counts = Counter(test["result"] for test in tests.values())
result = {
    "scope": "14 reproduced audit defects and related safety/lifecycle fixes",
    "python": {"unique_test_outcomes": dict(counts), "junit_evidence": evidence,
               "syntax_checked_files": compiled, "syntax_errors": syntax_errors},
    "javascript": {"node_tests": 109, "frontend_state_tests": 79, "browser_tests": 52,
                   "browser_lifecycle_after_metadata_fix": "passed; exact metadata hashes restored"},
    "builds": {"electron_harness_model_router": "passed", "frontend_typecheck": "passed",
               "frontend_lint": "passed", "frontend_production": "completed",
               "source_release_scan": "passed", "pip_check": "passed"},
    "root_npm_audit": {"vulnerabilities": 0, "network_verified": True},
    "changed_inventory_sources": sorted(changed),
    "added_inventory_sources": added,
    "other_changed_files": [".env.example", "README.md", "package.json", "package-lock.json",
                            "backend/model_router/data/provider-quarantine.json"],
    "unqualified": ["native test sandbox implementation", "quarantined SAP provider",
        "MCP", "Codex OAuth", "Google consumer subscription OAuth",
        "semantic agent loop consolidation", "live external provider accounts",
        "physical flash and serial cycles", "Linux/macOS process and installer behavior",
        "signed installer upgrade/uninstall", "power-loss recovery"],
    "environment": {"platform": "Windows", "python": "3.10.6", "node": "24.14.0",
        "npm": "11.9.0", "node_dependency_engine_caveat":
        "ini@7.0.0 declares ^22.22.2 || ^24.15.0 || >=26.0.0; current Node is outside that range"},
}
(HERE / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"python": result["python"],
    "changed_inventory_sources": result["changed_inventory_sources"]}, indent=2))
raise SystemExit(1 if syntax_errors or counts["failed"] or counts["error"] else 0)
