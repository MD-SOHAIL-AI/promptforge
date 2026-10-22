"""Run incident prompts through an installed desktop backend and real toolchain.

Requires a selected real provider, whose normal usage charges may apply. The
local token is read from the environment, never command arguments or reports.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import sys
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.agent_qualification import wait_for_job


def qualify(args):
    parsed = urlparse(args.url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.username or parsed.password:
        raise ValueError("QUALIFICATION_REQUIRES_LOCAL_BACKEND")
    token = os.environ.get("FORGEX_LOCAL_TOKEN", "")
    if len(token) < 32 or args.provider in {"fake_planner", "verified_template"}:
        raise ValueError("QUALIFICATION_REQUIRES_AUTH_AND_REAL_PROVIDER")
    installer_hash = hashlib.sha256(args.installer.read_bytes()).hexdigest()
    build = json.loads(Path(str(args.installer) + ".evidence.json").read_text())
    fixture_path = ROOT / "tests/fixtures/beta-agent-prompts.json"
    fixture_bytes = fixture_path.read_bytes()
    fixtures = json.loads(fixture_bytes)["cases"]
    args.workspace.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def request(route, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        with urllib.request.urlopen(urllib.request.Request(args.url.rstrip("/") + route, data=data, headers=headers), timeout=30) as response:
            return json.load(response)
    provenance = request("/diagnostics/runtime")
    if not provenance["packaged"] or not build.get("resourceManifestSha256") or provenance["resource_manifest_sha256"] != build["resourceManifestSha256"]:
        raise ValueError("QUALIFICATION_RUNTIME_DOES_NOT_MATCH_INSTALLER")
    results = []
    nonce = secrets.token_hex(12)
    for case in fixtures:
        case_root = args.workspace / (nonce + "-" + case["id"])
        case_root.mkdir()
        project = request("/projects/import", {"path": str(case_root.resolve())})
        request("/agent-runtime/projects/" + project["project_id"] + "/trust", {"trusted": True})
        session = request("/agent-runtime/sessions", {"project_id": project["project_id"]})["session"]
        turns = []
        for prompt in (case["request"], case["follow_up"]):
            response = request(f"/agent-runtime/sessions/{session['session_id']}/messages",
                               {"content": prompt, "provider_id": args.provider, "model_id": args.model, "autonomy": "auto", "board_type": "esp32", "environment": "esp32dev"})
            if response["session"].get("pending_clarification"):
                response = request(f"/agent-runtime/sessions/{session['session_id']}/messages",
                                   {"content": case["clarification_answer"], "provider_id": args.provider, "model_id": args.model, "autonomy": "auto", "board_type":"esp32", "environment":"esp32dev"})
            run = response.get("run")
            if not run:
                raise ValueError("QUALIFICATION_DID_NOT_START: " + case["id"])
            run, attempts = wait_for_job(request, run, time.monotonic() + 960)
            build_result = run.get("build_result") or {}
            requirements = run.get("requirement_report") or {}
            valid = run["status"] == "completed" and build_result.get("success") is True and requirements.get("valid") is True and requirements.get("verification_level") == "model_review" and requirements.get("workspace_revision") == build_result.get("workspace_revision")
            valid = valid and run.get("provider_id") == args.provider and run.get("model_id") == args.model and not run.get("flash_result") and not run.get("monitor_result")
            manifest = build_result.get("artifact_manifest") or {}
            application = next((image for image in manifest.get("images", []) if image.get("role") == "application"), None)
            binary = Path(manifest["artifact_root"]) / application["filename"] if application else Path(build_result.get("firmware_path") or "missing")
            valid = valid and binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == build_result.get("firmware_hash")
            turns.append({"run_id": run["run_id"], "passed": bool(valid), "status": run["status"],
                "classification": run.get("classification"), "provider": run.get("provider_id"), "model": run.get("model_id"),
                "task_revision": run.get("task_revision"), "firmware_sha256": build_result.get("firmware_hash"),
                "source_revision": build_result.get("workspace_revision"), "requirement_revision": requirements.get("workspace_revision"),
                "source_verified": requirements.get("valid") is True,
                "verification_level": requirements.get("verification_level"),
                "toolchain": build_result.get("toolchain"), "diagnostic_export": request("/diagnostics/runs/" + run["run_id"] + "/export")})
            print(case["id"], run["status"], flush=True)
            if not valid:
                break
        results.append({"id": case["id"], "passed": len(turns) == 2 and all(turn["passed"] for turn in turns), "turns": turns,
                        "review": {"passed": False, "reviewer": None, "evidence": None}})
        report = {"schema_version": 1, "installer_sha256": installer_hash,
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(), "resource_manifest_sha256": provenance["resource_manifest_sha256"],
            "backend_source_fingerprint": provenance["source_fingerprint"], "nonce": nonce,
            "automated_passed": len(results) == len(fixtures) and all(item["passed"] for item in results),
            "passed": False, "cases": results}
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if report["automated_passed"]:
        print("Automated prompt checks passed. Review each generated project against its request before release sign-off.", flush=True)
    return report["automated_passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(0 if qualify(parser.parse_args()) else 1)
