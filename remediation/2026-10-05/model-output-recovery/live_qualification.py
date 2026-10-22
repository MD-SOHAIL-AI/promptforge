"""Authorized synthetic OpenRouter qualification. No apply, flash, or serial.

Generation and compilation are separate commands so retrying an environment-
blocked compiler never sends another provider request. Credentials stay in memory.
"""
from __future__ import annotations

import argparse
import asyncio
import configparser
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent / "live-qualification"
MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
sys.path.insert(0, str(ROOT))


def now():
    return datetime.now(timezone.utc).isoformat()


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")


def progress(value):
    # Never print raw provider envelopes, reasoning, options, or credentials.
    safe = {key: value[key] for key in ("event_type", "status", "turn", "tool", "classification") if key in value}
    safe["timestamp"] = now()
    with (OUT / "progress.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe, default=str) + "\n")
    print(json.dumps(safe, default=str), flush=True)


def isolate():
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.update(FORGEX_ROOT=str(ROOT), FORGEX_DATA_DIR=str(OUT / "data"),
        FORGEX_LEGACY_DATA_DIR="", FORGEX_LIVE_LOG_PATH=str(OUT / "runtime.jsonl"),
        FORGEX_MODEL_ROUTER_SETTINGS_PATH=str(OUT / "settings.json"))


async def generate():
    if (OUT / "generation.json").exists():
        raise RuntimeError("Existing live result preserved; generation will not replay automatically")
    from backend.model_router.credentials import default_credential_store, MemoryCredentialStore
    from backend.model_router.storage import ProviderSettingsStorage
    from backend.model_router.registry import ProviderRegistry

    # Read real settings explicitly before isolating every writable service path.
    real_path = Path(os.environ["APPDATA"]) / "ForgeX/model-router/settings.json"
    secure = default_credential_store()
    real_storage = ProviderSettingsStorage(real_path, credential_store=secure)
    config = real_storage.provider_settings("openrouter")
    key = secure.get("openrouter") or config.get("api_key") or os.getenv("OPENROUTER_API_KEY")
    options_raw = secure.get("openrouter-options")
    options = json.loads(options_raw) if options_raw else {}
    key = key or options.get("apiKey") or options.get("token")
    if not key:
        raise RuntimeError("Configured OpenRouter credential is unavailable; no request was sent")
    endpoint = config.get("base_url") or "https://openrouter.ai/api/v1"
    if urlsplit(endpoint).hostname != "openrouter.ai" or urlsplit(endpoint).scheme != "https":
        raise RuntimeError("Configured endpoint is not HTTPS OpenRouter; no request was sent")
    isolate()
    from backend.model_router.router import ModelRouterService
    from backend.model_router.usage import UsageTracker
    from backend.agent_runtime.router_planner import RouterProductPlanner
    from backend.agent_runtime.tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.changes import ChangeSetService

    memory_store = MemoryCredentialStore()
    registry = ProviderRegistry(ProviderSettingsStorage(OUT / "settings.json", credential_store=memory_store))
    registry.configure_provider("openrouter", {"api_key": key, "options": options,
        "base_url": endpoint, "default_model": MODEL, "enabled": True})
    router = ModelRouterService(registry, usage=UsageTracker(OUT / "usage.jsonl"))
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id=MODEL)
    planner.bind_job_budget({"consumed": {}, "limits": {
        "requests": 16, "output_tokens": 65536, "input_bytes": 1000000}},
        lambda budget: save("budget.json", budget))
    active = OUT / "empty-active-project"
    active.mkdir(exist_ok=True)
    if list(active.iterdir()):
        raise RuntimeError("Synthetic active workspace must be empty")
    changes = ChangeSetService(state_root=OUT / "changes", staging_root=OUT / "staging")
    runtime = ForgeXToolRuntime(active_workspace_root=active, change_service=changes)
    platform_version = json.loads((Path.home() / ".platformio/platforms/espressif32/platform.json").read_text())["version"]
    task = (f"Create a minimal synthetic ESP32 Arduino project with exactly two files: platformio.ini "
        f"and src/main.cpp. In platformio.ini use only [env:esp32dev], platform = espressif32@{platform_version}, "
        "board = esp32dev, framework = arduino. In src/main.cpp include Arduino.h, initialize Serial "
        "at 115200, set GPIO 2 as output, and toggle it once each second while printing a short "
        "synthetic heartbeat. Use complete small write tool calls. Read back the two files to check "
        "them, then finish. Do not invoke builds, shell, network, dependency installs, hardware, "
        "skills, or subagents. The host will compile afterward. State clearly that compilation "
        "and hardware behavior have not been verified. Do not apply the staged changes.")
    started = now()
    save("request-scope.json", {"started_at": started, "provider": "openrouter", "model": MODEL,
        "task": task, "existing_project_files_sent": 0, "hardware_authorized": False,
        "request_ceiling": 16, "output_token_ceiling": 65536})
    print(json.dumps({"event_type": "live.synthetic.started", "provider": "openrouter", "model": MODEL}), flush=True)
    result = await runtime.run_product_async(task=task, planner=planner, policy=product_agent_policy(),
        limits=ProductRuntimeLimits(max_runtime_seconds=900, max_turns=8, max_created_files=2),
        event_callback=progress, environment="esp32dev")
    report = {"started_at": started, "finished_at": now(), "provider": "openrouter", "model": MODEL,
        "runtime_result": result.to_safe_dict(), "budget": planner.budget_snapshot(),
        "active_workspace_empty": not list(active.iterdir()), "hardware_verified": False,
        "platform_version": platform_version}
    if result.change_set_id:
        draft = changes.get(result.change_set_id)
        report["stage_path"] = draft.staging_root
        report["change_set_status"] = draft.status
        report["review_required"] = draft.review_required
        report["source_sha256"] = {name: hashlib.sha256((Path(draft.staging_root) / name).read_bytes()).hexdigest()
            for name in ("platformio.ini", "src/main.cpp") if (Path(draft.staging_root) / name).is_file()}
    save("generation.json", report)
    print(json.dumps({"event_type": "live.synthetic.finished", "classification": result.classification.value,
        "provider_classification": result.provider_classification, "change_set_id": result.change_set_id,
        "tools_executed": result.tool_execution_count, "budget": report["budget"]}), flush=True)


async def compile_stage():
    isolate()
    from backend.runtime.subprocess_mgr import SubprocessManager
    from backend.services.platformio_service import PlatformIOService
    report = json.loads((OUT / "generation.json").read_text(encoding="utf-8"))
    if not report.get("stage_path"):
        raise RuntimeError("Generation produced no staged project to compile")
    stage = Path(report["stage_path"]).resolve(strict=True)
    if not stage.is_relative_to((OUT / "staging").resolve()):
        raise RuntimeError("Compile target is outside the isolated managed staging root")
    # Inspect generated configuration before running a compiler: no model scripts,
    # package installs, hooks, external source paths, or extra environments.
    config = configparser.ConfigParser(interpolation=None)
    config.read(stage / "platformio.ini", encoding="utf-8")
    expected = {"platform": "espressif32@" + report["platform_version"], "board": "esp32dev", "framework": "arduino"}
    if config.defaults() or config.sections() != ["env:esp32dev"] or dict(config["env:esp32dev"]) != expected:
        raise RuntimeError("Generated PlatformIO configuration differs from the authorized synthetic configuration")
    paths = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file() and ".pio" not in p.parts)
    if paths != ["platformio.ini", "src/main.cpp"]:
        raise RuntimeError("Generated project contains unexpected files")
    manager = SubprocessManager()
    service = PlatformIOService(manager, executable=str(ROOT / ".venv/Scripts/pio.exe"),
        env={"PLATFORMIO_CORE_DIR": str(Path.home() / ".platformio")}, timeout_s=300,
        artifact_store=OUT / "sealed-artifacts")
    try:
        started = now()
        print(json.dumps({"event_type": "live.compile.started", "timestamp": started}), flush=True)
        result = await service.build(stage, environment="esp32dev")
        compilation = {"started_at": started, "finished_at": now(), "success": result.success,
            "status": str(result.status), "message": result.message, "metadata": result.metadata,
            "source_sha256": report.get("source_sha256"), "hardware_verified": False,
            "flash_performed": False, "active_workspace_empty": not list((OUT / "empty-active-project").iterdir())}
        save("compilation.json", compilation)
        (OUT / "compiler-output.log").write_text(result.process_result.combined_text if result.process_result else result.message,
            encoding="utf-8")
        print(json.dumps({"event_type": "live.compile.finished", "success": result.success,
            "status": str(result.status), "message": result.message}), flush=True)
        if not result.success:
            return 1
        return 0
    finally:
        await manager.kill_all()


async def verify_evidence():
    isolate()
    from backend.services.firmware_manifest import lock_upload_set, verify_upload_set
    generation = json.loads((OUT / "generation.json").read_text(encoding="utf-8"))
    compilation = json.loads((OUT / "compilation.json").read_text(encoding="utf-8"))
    attempts = []
    for path in sorted(OUT.glob("runtime.backend-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event") in {"provider.attempt.completed", "provider.attempt.failed"}:
                attempts.append(row)
    stage = Path(generation["stage_path"])
    source_unchanged = all(hashlib.sha256((stage / name).read_bytes()).hexdigest() == digest
        for name, digest in generation["source_sha256"].items())
    reviewed_hashes = json.loads((OUT.parent / "file-hashes.json").read_text(encoding="utf-8"))
    changed_since_regression = [name for name, digest in reviewed_hashes.items()
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    assert generation["runtime_result"]["classification"] == "TOOL_RUNTIME_PASS"
    assert compilation["success"] and source_unchanged and not changed_since_regression
    assert attempts and len(attempts) == generation["budget"]["requests"]
    assert all(row["provider"] == "openrouter" and row["model"] == MODEL
        and row["event"] == "provider.attempt.completed" and row["http_status"] == 200
        and row["completion_received"] and row["worker_exit_code"] == 0
        and not row["worker_cleanup_incomplete"] for row in attempts)
    assert not list((OUT / "empty-active-project").iterdir())
    manifest = compilation["metadata"]["artifact_manifest"]
    # Hash validation under read locks. This function never opens a device.
    verify_upload_set(manifest, store=OUT / "sealed-artifacts", chip="esp32", environment="esp32dev")
    with lock_upload_set(manifest) as paths:
        sealed_image_count = len(paths)
    summary = {"verified_at": now(), "status": "PASS", "provider": "openrouter", "model": MODEL,
        "physical_requests": len(attempts), "http_statuses": [row["http_status"] for row in attempts],
        "finish_reasons": [row["finish_reason"] for row in attempts],
        "provider_input_tokens": sum(row.get("input_tokens") or 0 for row in attempts),
        "provider_output_tokens": sum(row.get("output_tokens") or 0 for row in attempts),
        "provider_reasoning_tokens": sum(row.get("reasoning_tokens") or 0 for row in attempts),
        "job_output_budget_charge": generation["budget"]["output_tokens"],
        "tool_calls_executed": generation["runtime_result"]["tool_execution_count"],
        "semantic_repair_exercised_live": False, "provider_switches": 0,
        "all_workers_exited_cleanly": True, "compile_success": True,
        "source_unchanged_during_compile": source_unchanged, "active_workspace_empty": True,
        "review_required": generation["review_required"], "sealed_images_verified": sealed_image_count,
        "manifest_sha256": manifest["sha256"], "flash_performed": False, "hardware_verified": False,
        "files_changed_since_regression": changed_since_regression}
    save("qualification.json", summary)
    # Preserve previous probe failures with their actual operation label.
    if (OUT / "probe-error.json").exists():
        error = json.loads((OUT / "probe-error.json").read_text(encoding="utf-8"))
        (OUT / "probe-error.json").rename(OUT / (error["mode"] + "-probe-error.json"))
    evidence_names = ["generation.json", "compilation.json", "qualification.json", "compiler-output.log",
        "request-scope.json", "progress.jsonl", "usage.jsonl"]
    save("evidence-hashes.json", {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in evidence_names})
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("generate", "compile", "verify"))
    args = parser.parse_args()
    try:
        operation = {"generate": generate, "compile": compile_stage, "verify": verify_evidence}[args.mode]
        code = asyncio.run(operation())
        sys.exit(code or 0)
    except Exception as exc:
        # Only exception type is printed; unexpected SDK messages can contain
        # provider details. The controlled failure reason is saved locally.
        save("probe-error.json", {"timestamp": now(), "mode": args.mode,
            "error_type": type(exc).__name__, "message": str(exc) if isinstance(exc, RuntimeError) else "Probe setup/operation failed"})
        print(json.dumps({"event_type": "live.probe.failed", "error_type": type(exc).__name__}), flush=True)
        sys.exit(2)
