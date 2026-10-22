"""Replay saved requests into isolated projects with their explicitly selected model.

Reads historical state only. Does not apply user ChangeSets or access hardware.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import shutil
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("simple", "advanced", "ultra"), required=True)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--continue-from", type=Path)
    parser.add_argument("--finish-reviews", action="store_true")
    parser.add_argument("--turns", type=int, default=40)
    parser.add_argument("--extensions", type=int, default=20)
    args = parser.parse_args()
    prefix = {"simple":"agent-run-2b565", "advanced":"agent-run-b31a", "ultra":"agent-run-c016"}[args.case]
    with sqlite3.connect("file:" + (ROOT / ".promptforge/state/agent-orchestration.sqlite3").as_posix() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT payload_json FROM agent_runs WHERE run_id LIKE ?", (prefix + "%",)).fetchone()
    if not row:
        raise ValueError("Qualification source run was not found")
    source = json.loads(row[0])
    request = source.get("task_state", {}).get("request") or source["_recovery_context"]["instruction"]
    case = ROOT / "audit/2026-10-09" / ("live-log-implementation-" + args.case + "-" + datetime.now().strftime("%H%M%S"))
    case.mkdir(parents=True, exist_ok=False)
    (case / "request.txt").write_text(request, encoding="utf-8")
    from backend.model_router.storage import ProviderSettingsStorage
    live_storage = ProviderSettingsStorage()
    state = json.loads(live_storage.path.read_text(encoding="utf-8"))
    for settings in state.get("providers", {}).values():
        settings.pop("api_key", None)
    (case / "model-settings.json").write_text(json.dumps(state), encoding="utf-8")
    os.environ["FORGEX_DATA_DIR"] = str(case / "data")
    os.environ["FORGEX_MODEL_ROUTER_SETTINGS_PATH"] = str(case / "model-settings.json")
    from backend.model_router.registry import ProviderRegistry
    from backend.model_router.router import ModelRouterService
    from backend.model_router.usage import UsageTracker
    from backend.agent_runtime.router_planner import RouterProductPlanner
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.task_contract import create_task_contract
    from backend.agent_runtime.jobs import AgentJobStore
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    from backend.agent_runtime.lifecycle import bind_job_resources
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from backend.agent_runtime.tool_contracts import ToolName
    from backend.services.platformio_service import PlatformIOService
    from backend.runtime.subprocess_mgr import SubprocessManager
    from backend.changes import ChangeSetService
    from backend.agent_runtime.risk_review import review_changes
    from dataclasses import replace

    provider, model = source["provider_id"], source["model_id"]
    registry = ProviderRegistry(ProviderSettingsStorage(case / "model-settings.json", credential_store=live_storage.credential_store))
    router = ModelRouterService(registry=registry, usage=UsageTracker(case / "usage.jsonl"))
    stage = case / "project"
    checkpoint = {}
    prior = args.continue_from.resolve() if args.continue_from else None
    if prior:
        if not prior.is_relative_to((ROOT / "audit/2026-10-09").resolve()):
            raise ValueError("Qualification continuation must remain inside the audit directory")
        shutil.copytree(prior / "project", stage)
        with sqlite3.connect(prior / "state.sqlite3") as previous_db, sqlite3.connect(case / "state.sqlite3") as next_db:
            previous_db.backup(next_db)
        for line in (prior / "events.jsonl").read_text(encoding="utf-8").splitlines():
            candidate = json.loads(line)["fields"].get("_resume_checkpoint")
            if candidate:
                checkpoint = candidate
    else:
        stage.mkdir()
        (stage / "platformio.ini").write_text("[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\nmonitor_speed = 115200\n")
    contract = create_task_contract(request, project_id="qualification",session_id="qualification",workspace=stage,intent="firmware_generation")
    (case / "contract.json").write_text(json.dumps(contract, indent=2),encoding="utf-8")
    jobs = AgentJobStore(AgentOrchestrationStore(case / "state.sqlite3"))
    if prior:
        previous_result = json.loads((prior / "result.json").read_text(encoding="utf-8"))
        job = jobs.attach(previous_result["job"]["job_id"],"qualification-continued")
        checkpoint["elapsed_seconds"] = job["consumed"]["elapsed_seconds"]
        checkpoint["provider_budget"] = job["consumed"]
        run_id = "qualification-continued"
    else:
        job = jobs.create(project_id="qualification",session_id="qualification",workspace_root=stage,run_id="qualification")
        run_id = "qualification"
    planner = RouterProductPlanner(router,provider_id=provider,model_id=model,session_id="qualification")
    bind_job_resources(planner,jobs,job,run_id)
    manager = SubprocessManager()
    platformio = PlatformIOService(manager)
    async def progress(event, fields):
        record={"event":event,"at":datetime.now().isoformat(),"fields":fields}
        with (case / "events.jsonl").open("a",encoding="utf-8") as handle:
            handle.write(json.dumps(record,default=str)+"\n")
        if event in {"TURN_STARTED","BUILD_COMPLETED","BUILD_FAILED","BUILD_BLOCKED","VERIFICATION_COMPLETED","AGENT_LOOP_FAILED"}:
            print(args.case,event,fields.get("turn",fields.get("error_code","")),flush=True)
    profile = replace(BUILD_REPAIR_PROFILE,allowed_tools=BUILD_REPAIR_PROFILE.allowed_tools - {ToolName.SPAWN_SUBAGENT,ToolName.OBSERVE_SERIAL})
    loop=AutonomousForgeAgentLoop(workspace_root=stage,planner=planner,platformio=platformio,
        subprocess_manager=manager,progress=progress,task_contract=contract,trusted_project=True,
        policy=profile.policy(),max_runtime_seconds=args.seconds,max_turns=args.turns,max_progress_extension_turns=args.extensions,
        max_tool_calls=job["limits"]["tool_calls_used"],
        job_limits=job["limits"],job_consumed=job["consumed"],resume_checkpoint=checkpoint)
    started=time.monotonic()
    loop.max_progress_extension_turns = max(0, args.extensions)
    if args.finish_reviews and prior:
        from types import SimpleNamespace
        output = dict(previous_result)
        result = SimpleNamespace(success=output["success"],classification=output["classification"],build_verified=output["build_verified"])
        output["continued_from"] = str(prior / "result.json")
    else:
        result=await loop.run(task=request,require_build=True,environment="esp32dev")
        output=result.to_dict()
    jobs.record_elapsed(job["job_id"],job["consumed"]["elapsed_seconds"]+time.monotonic()-started)
    output.update(source_run_id=source["run_id"],selected_provider=provider,selected_model=model,
                  physical_attempt_accounting=jobs.attempt_accounting(job["job_id"]),job=jobs.get(job["job_id"]),hardware_verified=False)
    (case / "result.json").write_text(json.dumps(output,indent=2,default=str),encoding="utf-8")
    if result.success:
        empty=case / "empty-workspace"
        empty.mkdir()
        changes=ChangeSetService(state_root=case / "changes",staging_root=case)
        change_set=changes.create_from_stage(provider_id=provider,workspace_root=empty,stage_root=stage,
            baseline=changes.inspect_workspace(empty),authorized_paths=tuple(changes.inspect_workspace(stage).files))
        output["risk_review"]=await review_changes(router,jobs,{"change_set":change_set.to_dict(),"risk":{"qualification":True},
            "routing":{"job_id":job["job_id"],"run_id":run_id,"provider_id":provider,"model_id":model}})
        jobs.record_elapsed(job["job_id"],job["consumed"]["elapsed_seconds"]+time.monotonic()-started)
        output["job"] = jobs.get(job["job_id"])
        output["physical_attempt_accounting"] = jobs.attempt_accounting(job["job_id"])
        warm_started=time.monotonic()
        warm=await platformio.build(stage,environment="esp32dev")
        output["warm_build"]={"success":warm.success,"duration_seconds":time.monotonic()-warm_started}
    (case / "result.json").write_text(json.dumps(output,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"evidence":str(case),"success":result.success,"classification":result.classification,"build_verified":result.build_verified}),flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
