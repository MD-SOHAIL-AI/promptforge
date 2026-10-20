"""Run 30 ESP32 scenarios three times with an explicitly selected real model.

Run against a packaged local backend. Normal provider charges apply. Reports
record automated evidence separately from the required project-fidelity review.
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.agent_qualification import wait_for_job, recovery_metrics
SUITE=ROOT/"tests/fixtures/agent-quality-suite.json"
TERMINAL={"completed","completed_with_warning","failed","blocked","cancelled","timed_out","paused_model_unavailable"}


def assess_turn(case,run,root,provider,model):
    build=run.get("build_result") or {}
    requirements=run.get("requirement_report") or {}
    acceptance=requirements.get("acceptance") or {}
    valid=run.get("status")==case["expected_status"] and run.get("provider_id")==provider and run.get("model_id")==model
    valid=valid and run.get("actual_provider_id",provider)==provider and not run.get("flash_result") and not run.get("monitor_result")
    valid=valid and (root/"unrelated-user.txt").read_text()=="preserve this exact user file\n"
    if "unchanged_workspace" in case["required_evidence"]:
        valid=valid and run.get("active_workspace_unchanged") is True and not run.get("change_set_id")
        valid=valid and all((root/name).read_text()==content for name,content in case.get("seed_files",{}).items())
    if "current_source" in case["required_evidence"]:
        valid=valid and requirements.get("valid") is True and requirements.get("verification_level") == "model_review" and requirements.get("workspace_revision")==build.get("workspace_revision")
        valid=valid and requirements.get("task_revision") == run.get("task_revision")
    if "current_firmware_build" in case["required_evidence"]:
        manifest = build.get("artifact_manifest") or {}
        application = next((item for item in manifest.get("images", []) if item.get("role") == "application"), None)
        binary=Path(manifest["artifact_root"]) / application["filename"] if application else Path(build.get("firmware_path") or "missing")
        valid=valid and build.get("success") is True and binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest()==build.get("firmware_hash")
        if case.get("platformio_board"):
            valid=valid and build.get("board") == case["platformio_board"] and build.get("environment") == case["environment"]
    if "native_tests" in case["required_evidence"]:
        valid=valid and acceptance.get("passed") is True and any(item.get("kind")=="unit_tests" and item.get("passed") is True for item in acceptance.get("checks",[]))
    job=run.get("job") or {}
    budget=job.get("budget") or {}
    return {"run_id":run.get("run_id"),"passed":bool(valid),"status":run.get("status"),"classification":run.get("classification"),
        "provider":run.get("provider_id"),"model":run.get("model_id"),"task_revision":run.get("task_revision"),
        "actual_provider":run.get("actual_provider_id", provider), "actual_model":run.get("model_id"),
        "requirement_revision":requirements.get("workspace_revision"), "build_revision":build.get("workspace_revision"),
        "review_task_revision":requirements.get("task_revision"), "execution_profile":job.get("execution_profile"),
        "board":build.get("board"), "environment":build.get("environment"),
        "consumed":budget.get("consumed", job.get("consumed", {})), "limits":budget.get("limits", job.get("limits", {})),
        "completion_review":requirements.get("completion_review"),
        "source_revision":requirements.get("workspace_revision"),"firmware_sha256":build.get("firmware_hash"),
        "source_verified":requirements.get("valid") is True,"verification_level":requirements.get("verification_level"),"acceptance":acceptance,"job":run.get("job")}


def qualify(args):
    parsed=urlparse(args.url)
    token=os.environ.get("FORGEX_LOCAL_TOKEN","")
    if parsed.scheme!="http" or parsed.hostname not in {"127.0.0.1","localhost"} or parsed.username or parsed.password:
        raise ValueError("QUALIFICATION_REQUIRES_LOCAL_BACKEND")
    if len(token)<32 or args.provider in {"fake_planner","verified_template"}:
        raise ValueError("QUALIFICATION_REQUIRES_AUTH_AND_REAL_PROVIDER")
    suite_kind = getattr(args, "suite", "generation")
    suite_path = (ROOT / "tests/fixtures/agent-maintenance-suite.json" if suite_kind == "maintenance" else
                  ROOT / "tests/fixtures/agent-target-suite.json" if suite_kind == "targets" else SUITE)
    suite=json.loads(suite_path.read_text())
    minimum, total = (57, 60) if suite_kind == "maintenance" else (len(suite["cases"])*3, len(suite["cases"])*3) if suite_kind == "targets" else (86, 90)
    installer_hash=hashlib.sha256(args.installer.read_bytes()).hexdigest()
    build=json.loads(Path(str(args.installer)+".evidence.json").read_text())
    def request(route,body=None):
        headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"}
        data=json.dumps(body).encode() if body is not None else None
        with urllib.request.urlopen(urllib.request.Request(args.url.rstrip("/")+route,data=data,headers=headers),timeout=35) as response:
            return json.load(response)
    provenance=request("/diagnostics/runtime")
    if not provenance["packaged"] or not build.get("resourceManifestSha256") or provenance["resource_manifest_sha256"]!=build["resourceManifestSha256"]:
        raise ValueError("QUALIFICATION_RUNTIME_DOES_NOT_MATCH_INSTALLER")
    args.workspace.mkdir(parents=True,exist_ok=True)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    def endpoint_hash():
        provider=next(item for item in request("/models/providers")["providers"] if item["provider_id"] == args.provider)
        return hashlib.sha256((provider.get("base_url") or "").rstrip("/").encode()).hexdigest()
    report={"schema_version":2,"suite_version":suite["suite_version"],"fixture_sha256":hashlib.sha256(suite_path.read_bytes()).hexdigest(),
        "endpoint_sha256":endpoint_hash(),
        "execution_profile": args.execution_profile,
        "installer_sha256":installer_hash,"resource_manifest_sha256":provenance["resource_manifest_sha256"],
        "backend_source_fingerprint":provenance["source_fingerprint"],"provider":args.provider,"model":args.model,
        "nonce":secrets.token_hex(12),"repetitions":3,"cases":[],"automated_passed":False,"passed":False}
    for case in suite["cases"]:
        result={"id":case["id"],"historical":case["historical"],"safety_critical":case.get("safety_critical", False),"attempts":[],"review":{"passed":False,"reviewer":None,"evidence":None}}
        report["cases"].append(result)
        for repetition in range(3):
            root=args.workspace/(report["nonce"]+"-"+case["id"]+"-"+str(repetition+1))
            root.mkdir()
            (root/"unrelated-user.txt").write_text("preserve this exact user file\n")
            for name,content in case.get("seed_files",{}).items():
                target=root/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(content)
            project=request("/projects/import",{"path":str(root.resolve())})
            request("/agent-runtime/projects/"+project["project_id"]+"/trust",{"trusted":True})
            session=request("/agent-runtime/sessions",{"project_id":project["project_id"]})["session"]
            turns=[]
            for prompt in (case["request"],case["follow_up"]):
                route="/agent-runtime/sessions/"+session["session_id"]+"/messages"
                settings={"provider_id":args.provider,"model_id":args.model,"autonomy":"auto","board_type":case.get("board_type", "esp32"),"environment":case.get("environment", "esp32dev"),
                    "execution_profile": args.execution_profile}
                response=request(route,{**settings,"content":prompt})
                if response["session"].get("pending_clarification") and case.get("clarification_answer"):
                    response=request(route,{**settings,"content":case["clarification_answer"]})
                run=response.get("run")
                if not run:
                    turns.append({"passed":False,"classification":"QUALIFICATION_DID_NOT_START"})
                    break
                started = time.monotonic()
                run, attempts = wait_for_job(request, run,
                    started + (2760 if args.execution_profile == "advanced" else 960))
                current_provenance = request("/diagnostics/runtime")
                evidence=assess_turn(case,run,root,args.provider,args.model)
                evidence["backend_source_fingerprint"] = current_provenance["source_fingerprint"]
                evidence["resource_manifest_sha256"] = current_provenance.get("resource_manifest_sha256")
                if (evidence["backend_source_fingerprint"] != report["backend_source_fingerprint"]
                        or evidence["resource_manifest_sha256"] != report["resource_manifest_sha256"]
                        or endpoint_hash() != report["endpoint_sha256"]):
                    evidence["passed"] = False
                evidence["recovery_metrics"] = recovery_metrics(attempts, time.monotonic() - started)
                evidence["diagnostic_export"]=request("/diagnostics/runs/"+run["run_id"]+"/export")
                turns.append(evidence)
                if not evidence["passed"]:
                    break
            result["attempts"].append({"repetition":repetition+1,"passed":len(turns)==2 and all(turn["passed"] for turn in turns),"turns":turns})
            args.output.write_text(json.dumps(report,indent=2)+"\n")
            print(case["id"],repetition+1,result["attempts"][-1]["passed"],flush=True)
    attempts=[attempt for case in report["cases"] for attempt in case["attempts"]]
    report["pass_rate"]=sum(attempt["passed"] for attempt in attempts)/total
    report["automated_passed"]=(sum(attempt["passed"] for attempt in attempts)>=minimum
        and all(sum(attempt["passed"] for attempt in case["attempts"])>=2 for case in report["cases"])
        and all(attempt["passed"] for case in report["cases"] if case["historical"] or case.get("safety_critical") for attempt in case["attempts"])
        and len(attempts) == total)
    args.output.write_text(json.dumps(report,indent=2)+"\n")
    return report["automated_passed"]


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--url",default="http://127.0.0.1:8000")
    parser.add_argument("--suite",choices=("generation", "maintenance", "targets"),default="generation")
    parser.add_argument("--installer",type=Path,required=True)
    parser.add_argument("--workspace",type=Path,required=True)
    parser.add_argument("--provider",required=True)
    parser.add_argument("--model",required=True)
    parser.add_argument("--execution-profile",choices=("standard","advanced"),default="standard")
    parser.add_argument("--output",type=Path,required=True)
    raise SystemExit(0 if qualify(parser.parse_args()) else 1)
