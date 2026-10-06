"""Exercise durable Stop/outage/restart/resume using the bundled backend.

Provider outcomes are injected; no live model, user project or device is used.
The real ESP32 compiler is covered separately by smoke-esp32-build.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time


async def smoke(bundle: Path, output: Path) -> None:
    bundle = bundle.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="reliability-", dir=output.resolve()))
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", FORGEX_ROOT=str(bundle), FORGEX_DATA_DIR=str(profile / "data"),
                      FORGEX_LEGACY_DATA_DIR="", FORGEX_LIVE_LOG_PATH=str(profile / "runtime.jsonl"))
    sys.path.insert(0, str(bundle))
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
    from backend.agent_runtime.run_models import ProductAgentRun
    from backend.agent_runtime.tool_contracts import ToolCall, ToolName
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.changes import ChangeSetService
    from backend.model_router.credentials import MemoryCredentialStore
    from backend.model_router.registry import ProviderRegistry
    from backend.model_router.storage import ProviderSettingsStorage
    from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT

    manifest = json.loads((bundle / "release-manifest.json").read_text(encoding="utf-8"))
    assert BACKEND_SOURCE_FINGERPRINT == manifest["backendSourceFingerprint"]
    reports = []
    for mode in ("stop", "outage"):
        state = profile / mode
        workspace = state / "workspace"
        workspace.mkdir(parents=True)
        changes = ChangeSetService(state_root=state / "changes", staging_root=state / "stages")
        models = ProviderRegistry(ProviderSettingsStorage(state / "providers.json", credential_store=MemoryCredentialStore()))
        providers = ProductProviderRegistry(model_provider_registry=models, fake_enabled=True)
        ready = asyncio.Event()
        resumed = []
        csv = "# Name,Type,SubType,Offset,Size\nnvs,data,nvs,,0x5000\napp0,app,factory,,1M\n"
        files = {"partitions.csv": csv, "src/main.cpp": "#include <Arduino.h>\nvoid setup(){}\nvoid loop(){}\n",
                 "platformio.ini": "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nboard_build.partitions=partitions.csv\n"}

        async def executor(request):
            stage = Path(request.active_workspace_root)
            if request.active_workspace.get("agent_resume_checkpoint", {}).get("turn", 0):
                assert all((stage / path).read_text(encoding="utf-8") == content for path, content in files.items())
                resumed.append(request.run_id)
                # Verify continuation dispatch and preserved source without
                # pretending this injected executor performed a real build.
                return {"success": False, "classification": "AGENT_BUILD_TOOLCHAIN_UNAVAILABLE",
                        "message": "Injected executor completed source recovery; compiler checked separately"}
            written = []
            for index, (path, content) in enumerate(files.items(), 1):
                product_agent_policy().validate_call(ToolCall(ToolName.WRITE_FILE, {"path": path, "content": content}), stage)
                target = stage / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                written.append(path)
                await request.progress_callback("tool.checkpoint", {"turn": 1, "call_id": str(index),
                    "_resume_checkpoint": {"turn": 1, "observations": [], "authorized_paths": list(written),
                        "deleted_paths": [], "tool_calls_used": index, "elapsed_seconds": 1}})
            ready.set()
            if mode == "stop":
                await asyncio.Event().wait()
            return {"success": False, "classification": "PROVIDER_UNAVAILABLE", "message": "Injected provider 503"}

        def owner():
            result = AgentOrchestrator(change_service=changes, provider_registry=providers,
                store=AgentOrchestrationStore(state / "journal.sqlite3"), run_factory=ProductAgentRun,
                autonomous_executor=executor)
            result.auto_recovery_enabled = False
            return result

        initial = owner()
        run = await initial.start_run(project_id=mode, active_workspace_root=workspace,
            instruction="Generate ESP32 firmware", provider_id="verified_template", autonomy="staged_changes",
            active_workspace={"agent_requires_build": True}, session_id=mode)
        await asyncio.wait_for(ready.wait(), 10)
        stop_seconds = None
        if mode == "stop":
            started = time.monotonic()
            await asyncio.gather(initial.cancel(run.run_id), initial.cancel(run.run_id))
            stop_seconds = time.monotonic() - started
            assert stop_seconds < 10 and run.status == "cancelled"
            assert len([event for event in run.events if event["event_type"] == "runtime.cancelled"]) == 1
        else:
            await initial._tasks[run.run_id]
            assert run.status == "paused_model_unavailable"
        assert run.created_file_count == len(files) and run.change_set_id
        draft = changes.get(run.change_set_id)
        assert draft.status == "draft" and not list(workspace.iterdir())
        changes.prune_review_drafts(workspace, keep=0)
        assert all((Path(draft.staging_root) / path).read_text(encoding="utf-8") == content for path, content in files.items())
        initial.jobs.trust(mode, str(workspace.resolve()), True)
        initial.jobs.persist_provider_budget(run.job_id, {"requests": 4, "output_tokens": 1024, "input_bytes": 100})
        await initial.close()
        reopened = owner()
        restored = reopened.get_run(run.run_id)
        info = reopened.recovery_info(run.run_id)
        assert restored.change_set_id == draft.change_set_id and info["eligible"]
        replacement = await reopened.resume(run.run_id, checkpoint_id=info["checkpoint_id"], idempotency_key=mode + "-resume")
        await reopened._tasks[replacement.run_id]
        assert resumed == [replacement.run_id]
        assert (replacement.provider_id, replacement.model_id) == (run.provider_id, run.model_id)
        assert reopened.jobs.get(run.job_id)["consumed"]["requests"] == 4
        assert not list(workspace.iterdir())
        await reopened.discard_draft(run.run_id)
        assert changes.get(draft.change_set_id).status == "discarded"
        assert not reopened.recovery_info(run.run_id)["eligible"]
        reports.append({"mode": mode, "passed": True, "saved_files": len(files), "stop_seconds": stop_seconds,
                        "run_id": run.run_id, "replacement_run_id": replacement.run_id,
                        "provider_unchanged": True, "workspace_unchanged": True})
        await reopened.close()

    report = {"schema_version": 1, "passed": True, "backend_source_fingerprint": BACKEND_SOURCE_FINGERPRINT,
              "app_version": manifest["appVersion"], "provider_outcomes_injected": True,
              "hardware_used": False, "cases": reports, "isolated_profile": str(profile)}
    path = output / "live-log-reliability-smoke.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Bundled Stop/outage/restart/resume/discard passed: " + str(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(smoke(args.bundle, args.output))
