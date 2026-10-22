"""Assemble existing reports for the release verifier; never run a model or mark a pass."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    path = Path(path)
    if path.stat().st_size > 64_000_000:
        raise ValueError("QUALIFICATION_REPORT_TOO_LARGE")
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1_048_576), b""):
            result.update(chunk)
    return result.hexdigest()


def assemble(config_path, installer, incident_reports):
    config_path, installer = Path(config_path), Path(installer)
    config = read_json(config_path)
    if config.get("schema_version") != 1 or not isinstance(config.get("models"), list):
        raise ValueError("QUALIFICATION_CONFIGURATION_INVALID")
    build = read_json(str(installer) + ".evidence.json")
    rows, fingerprints = [], set()
    for model in config["models"]:
        reports = {}
        for kind in ("generation", "maintenance", "targets"):
            value = model.get("reports", {}).get(kind)
            if not isinstance(value, str) or not value:
                raise ValueError("QUALIFICATION_REPORT_REQUIRED: " + str(model.get("id")) + "/" + kind)
            report_path = Path(value)
            report_path = report_path if report_path.is_absolute() else config_path.parent / report_path
            reports[kind] = read_json(report_path)
            fingerprints.add(reports[kind].get("backend_source_fingerprint"))
        rows.append({"id": model["id"], "endpoint_sha256": model["endpoint_sha256"], **reports})
    incidents = [read_json(path) for path in incident_reports]
    fingerprints.update(item.get("backend_source_fingerprint") for item in incidents)
    if len(fingerprints) != 1 or not all(isinstance(item, str) and len(item) == 64 for item in fingerprints):
        raise ValueError("QUALIFICATION_RUNTIME_FINGERPRINT_MISMATCH")
    # Use the verifier's encoder, including JavaScript number/key ordering.
    node = shutil.which("node")
    if not node:
        raise ValueError("QUALIFICATION_NODE_REQUIRED")
    module = (ROOT / "scripts/verify-runtime-reliability.mjs").as_uri()
    encoded = subprocess.run([node, "--input-type=module", "-e",
        "import {configurationHash} from " + json.dumps(module) + ";"
        "let input='';for await(const chunk of process.stdin)input+=chunk;"
        "process.stdout.write(configurationHash(JSON.parse(input)));"],
        input=json.dumps(config), capture_output=True, text=True, check=True, timeout=20).stdout
    return {"schema_version": 1, "config": config,
        "config_sha256": encoded, "installer_sha256": digest(installer),
        "resource_manifest_sha256": build["resourceManifestSha256"],
        "backend_source_fingerprint": next(iter(fingerprints)),
        "incident_fixture_sha256": digest(ROOT / "tests/fixtures/runtime-incident-smart-monitor.json"),
        "models": rows, "incident": incidents}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--incident-report", required=True, type=Path, action="append")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if len(args.incident_report) != 3:
        parser.error("Exactly three fresh incident reports are required")
    report = assemble(args.config, args.installer, args.incident_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as file:
        file.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"assembled": True, "models": len(report["models"]), "release_verified": False,
        "next_action": "Run npm run verify:release with this evidence bundle."}))
