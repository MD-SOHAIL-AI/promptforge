"""Replay a recorded request over real HTTP in an isolated ForgeX data profile.

Only ForgeX writes firmware. Historical projects/drafts are read-only inputs.
Uses the source run's selected provider, model and execution profile.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class QualificationApiError(RuntimeError):
    def __init__(self, method, path, status, code):
        self.code = code
        super().__init__(f"Qualification API {method} {path}: {status} {code}")


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1_048_576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def packaged_runtime(root, installer):
    """Bind an installed runtime to its installer before making model calls."""
    root, installer = Path(root).resolve(strict=True), Path(installer).resolve(strict=True)
    manifest_path = root / "release-manifest.json"
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = json.loads(Path(str(installer) + ".evidence.json").read_text(encoding="utf-8"))
    digest = file_sha256(installer)
    if build.get("sha256") != digest or build.get("resourceManifestSha256") != manifest_hash:
        raise ValueError("QUALIFICATION_INSTALLER_RUNTIME_MISMATCH")
    for relative, expected in manifest.get("files", {}).items():
        file = (root / relative).resolve(strict=True)
        file.relative_to(root)
        if file_sha256(file) != expected:
            raise ValueError("QUALIFICATION_PACKAGED_FILE_CHANGED")
    python = root / "python/python.exe"
    if not manifest.get("files") or not python.is_file() or not (root / "forgex-pio.py").is_file():
        raise ValueError("QUALIFICATION_PACKAGED_RUNTIME_INCOMPLETE")
    return root, python, manifest_hash, digest


async def main():
    import httpx
    from backend.runtime.windows_job import WindowsJob
    parser = argparse.ArgumentParser()
    source_args = parser.add_mutually_exclusive_group(required=True)
    source_args.add_argument("--source-run")
    source_args.add_argument("--fixture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--packaged-root", type=Path, help="Installed resources/forgex directory; uses its bundled Python and PlatformIO")
    parser.add_argument("--installer", type=Path, help="Exact installer and adjacent .evidence.json for packaged qualification")
    parser.add_argument("--node-executable", type=Path, help="Installed ForgeX executable for its Node worker")
    parser.add_argument("--continue-run", help="Continue a saved run in the existing isolated output profile")
    args = parser.parse_args()
    if bool(args.packaged_root) != bool(args.installer):
        parser.error("--packaged-root and --installer must be supplied together")
    runtime_root, runtime_python, manifest_hash, installer_hash = (
        packaged_runtime(args.packaged_root, args.installer) if args.packaged_root else (ROOT, Path(sys.executable), None, None))
    case = args.output.resolve()
    if case.exists() and not args.continue_run:
        raise ValueError("Qualification requires a new output directory")
    case.relative_to((ROOT / ".forgex").resolve())
    historical = ROOT / ".promptforge/state"
    fixture = None
    if args.fixture:
        fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
        request = fixture["request"]
        if fixture.get("schema_version") != 1 or hashlib.sha256(request.encode()).hexdigest() != fixture.get("request_sha256"):
            raise ValueError("Incident fixture checksum or version is invalid")
        source = {"provider_id": fixture["provider_id"], "model_id": fixture["model_id"], "flash_board_type": fixture["board_type"]}
        profile = fixture["execution_profile"]
        require_completion = fixture.get("require_completion_review") is True
    else:
        with sqlite3.connect((historical / "agent-orchestration.sqlite3").as_uri()+"?mode=ro", uri=True) as db:
            row = db.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (args.source_run,)).fetchone()
        if row is None:
            raise ValueError("Source run was not found")
        source = json.loads(row[0])
        contract = source["_recovery_context"]["active_workspace"]["task_contract"]
        request = contract["request"]
        profile = source.get("job", {}).get("execution_profile", "standard")
        require_completion = len(contract.get("requirements", ())) >= 8
    if profile not in {"standard", "advanced"} or source["provider_id"] in {"fake_planner", "verified_template"}:
        raise ValueError("Qualification requires a real explicitly selected model and bounded profile")
    if args.continue_run and not (case / "data/.promptforge/state/agent-orchestration.sqlite3").is_file():
        raise ValueError("Continuation requires an existing isolated qualification profile")
    case.mkdir(parents=True, exist_ok=bool(args.continue_run))
    evidence = case / ("continuation-" + uuid.uuid4().hex if args.continue_run else ".")
    evidence.mkdir(exist_ok=True)
    state = case / "data/.promptforge/state"
    state.mkdir(parents=True, exist_ok=bool(args.continue_run))
    for name in ("model-router.sqlite3", "model-router.selections.sqlite3"):
        if not args.continue_run and (historical / name).exists():
            with sqlite3.connect((historical / name).as_uri()+"?mode=ro", uri=True) as old, sqlite3.connect(state / name) as new:
                old.backup(new)
    if not args.continue_run and (historical / "model-router.json").exists():
        shutil.copy2(historical / "model-router.json", state / "model-router.json")
    project = case / "project"
    project.mkdir(exist_ok=bool(args.continue_run))
    token = secrets.token_urlsafe(48)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = dict(os.environ)
    environment.update(FORGEX_ROOT=str(runtime_root), FORGEX_DATA_DIR=str(case / "data"),
        FORGEX_SETTINGS_PATH=str(case / "settings.json"), FORGEX_LOCAL_TOKEN=token,
        FORGEX_BACKEND_PORT=str(port), FORGEX_ENABLE_AGENT_RUNTIME="1",
        FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2="1", FORGEX_ENABLE_SAFE_AUTO_RECOVERY="0",
        FORGEX_NODE_EXECUTABLE=str(args.node_executable) if args.node_executable else shutil.which("node") or "node",
        FORGEX_PLATFORMIO_EXECUTABLE=str(runtime_root / "forgex-pio.py") if args.packaged_root else str(ROOT / ".venv/Scripts/pio.exe"),
        FORGEX_LIVE_LOG_PATH=str(evidence / "runtime.jsonl"))
    if args.packaged_root:
        environment["FORGEX_DESKTOP"] = "1"
    else:
        environment.pop("FORGEX_DESKTOP", None)
    for name in ("FORGEX_MODEL_ROUTER_SETTINGS_PATH", "FORGEX_LEGACY_DATA_DIR", "PROMPTFORGE_ROOT"):
        environment.pop(name, None)
    argv = [str(runtime_python), "-B", "-m", "uvicorn", "backend.api.app:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port), "--no-access-log"]
    job = WindowsJob() if os.name == "nt" else None
    samples = []
    current = None
    last_status = None
    try:
        with (evidence / "backend.log").open("w", encoding="utf-8") as log:
            child = subprocess.Popen(job.wrap(argv) if job else argv, cwd=runtime_root, env=environment,
                stdout=log, stderr=subprocess.STDOUT,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization":"Bearer " + token}, timeout=30) as client:
                async def call(method, path, **kwargs):
                    response = await client.request(method, path, **kwargs)
                    if response.is_error:
                        try:
                            body = response.json()
                        except ValueError:
                            body = {}
                        raise QualificationApiError(method, path, response.status_code,
                            str(body.get("code", "HTTP_ERROR")))
                    response.raise_for_status()
                    return response.json()
                deadline = time.monotonic() + 180
                while True:
                    try:
                        health = await call("GET", "/health")
                        if health.get("service") == "forgex-backend" and health.get("engine_ready"):
                            break
                    except (httpx.HTTPError, QualificationApiError):
                        pass
                    if child.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("Owned backend did not become ready")
                    await asyncio.sleep(.5)
                initial_provenance = await call("GET", "/diagnostics/runtime")
                if args.packaged_root and (initial_provenance.get("packaged") is not True
                        or initial_provenance.get("resource_manifest_sha256") != manifest_hash):
                    raise ValueError("QUALIFICATION_PACKAGED_PROVENANCE_MISMATCH")
                if args.continue_run:
                    previous = (await call("GET", f"/agent-runtime/runs/{args.continue_run}"))["run"]
                    recovery = previous.get("recovery") or {}
                    if not recovery.get("actions", {}).get("continue_generation", {}).get("allowed"):
                        raise RuntimeError("Saved run does not permit continuation: " + str(recovery.get("blocking_reason")))
                    admitted = await call("POST", f"/agent-runtime/runs/{args.continue_run}/resume", json={
                        "checkpoint_id": recovery["checkpoint_id"], "idempotency_key": "qualification-" + uuid.uuid4().hex})
                else:
                    imported = await call("POST", "/projects/import", json={"path":str(project)})
                    project_id = imported["project_id"]
                    await call("POST", f"/agent-runtime/projects/{project_id}/trust", json={"trusted":True})
                    admitted = await call("POST", "/agent-runtime/runs", json={
                        "project_id":project_id, "instruction":request, "provider_id":source["provider_id"],
                        "model_id":source["model_id"], "autonomy":"build_only", "execution_profile":profile,
                        "board_type":source.get("flash_board_type") or "ESP32", "environment":"esp32dev"})
                run_id = admitted["run"]["run_id"]
                def endpoint_hash(providers):
                    provider = next(item for item in providers["providers"] if item["provider_id"] == source["provider_id"])
                    return hashlib.sha256((provider.get("base_url") or "").rstrip("/").encode()).hexdigest()
                endpoint_fingerprint = endpoint_hash(await call("GET", "/models/providers"))
                print(json.dumps({"run_id":run_id, "source_run":args.source_run, "profile":profile}), flush=True)
                deadline = time.monotonic() + (2700 if profile == "advanced" else 900) + 120
                while time.monotonic() < deadline:
                    started = time.monotonic()
                    try:
                        await call("GET", "/health")
                        samples.append({"duration_ms":round((time.monotonic()-started)*1000), "passed":True})
                    except (httpx.HTTPError, QualificationApiError):
                        samples.append({"duration_ms":round((time.monotonic()-started)*1000), "passed":False})
                    current = (await call("GET", f"/agent-runtime/runs/{run_id}"))["run"]
                    if current["status"] != last_status:
                        print(json.dumps({"status":current["status"], "classification":current.get("classification")}), flush=True)
                        last_status = current["status"]
                    if current["status"] in {"completed", "completed_with_warning", "failed", "blocked", "timed_out", "cancelled", "paused_model_unavailable"}:
                        break
                    await asyncio.sleep(1)
                else:
                    await call("POST", f"/agent-runtime/runs/{run_id}/cancel")
                    raise RuntimeError("Qualification exceeded the bounded job allowance")
                (evidence / "run.json").write_text(json.dumps(current, indent=2), encoding="utf-8")
                build = current.get("build_result") or {}
                requirements = current.get("requirement_report") or {}
                completion = requirements.get("completion_review") or {}
                task_revision = current.get("task_revision")
                revision = build.get("workspace_revision")
                firmware_valid = False
                manifest = build.get("artifact_manifest") or {}
                application = next((item for item in manifest.get("images", ()) if item.get("role") == "application"), None)
                binary = Path(manifest["artifact_root"]) / application["filename"] if application else Path(build.get("firmware_path") or case / "missing")
                try:
                    binary.resolve().relative_to(case)
                    firmware_valid = binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == build.get("firmware_hash")
                except (OSError, ValueError):
                    pass
                latest_health = await call("GET", "/health")
                provenance = await call("GET", "/diagnostics/runtime")
                provider_current = endpoint_hash(await call("GET", "/models/providers"))
                job_state = current.get("job") or {}
                budget = job_state.get("budget") or {}
                limits = budget.get("limits", job_state.get("limits", {}))
                consumed = budget.get("consumed", job_state.get("consumed", {}))
                passed = (current["status"] == "completed" and current.get("build_verified") is True and firmware_valid
                    and requirements.get("valid") is True and requirements.get("verification_level") == "model_review"
                    and bool(revision) and requirements.get("workspace_revision") == revision
                    and bool(task_revision) and requirements.get("task_revision") == task_revision
                    and (not require_completion or completion.get("passed") is True and completion.get("source_revision") == revision
                         and completion.get("task_revision") == task_revision)
                    and latest_health.get("source_fingerprint") == health.get("source_fingerprint")
                    and provider_current == endpoint_fingerprint
                    and (not fixture or limits == fixture["limits"])
                    and provenance.get("resource_manifest_sha256") == initial_provenance.get("resource_manifest_sha256"))
                timings = sorted(sample["duration_ms"] for sample in samples)
                report = {"source_run":args.source_run, "run_id":run_id, "provider":source["provider_id"],
                    "model":source["model_id"], "profile":profile, "passed":passed,
                    "status":current["status"], "classification":current.get("classification"),
                    "build_verified":current.get("build_verified"), "hardware_verified":False,
                    "continued_from":args.continue_run,
                    "backend_fingerprint":health.get("source_fingerprint"), "health_samples":len(samples),
                    "backend_source_fingerprint": provenance.get("source_fingerprint"),
                    "resource_manifest_sha256": provenance.get("resource_manifest_sha256"),
                    "installer_sha256": installer_hash,
                    "endpoint_sha256": endpoint_fingerprint, "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
                    "actual_provider": current.get("actual_provider_id", current.get("provider_id")), "actual_model": current.get("model_id"),
                    "execution_profile": profile, "consumed": consumed, "limits": limits,
                    "source_verified": requirements.get("valid") is True, "verification_level": requirements.get("verification_level"),
                    "source_revision": revision, "build_revision": revision, "requirement_revision": requirements.get("workspace_revision"),
                    "task_revision": task_revision, "review_task_revision": requirements.get("task_revision"),
                    "completion_review_passed": completion.get("passed") is True,
                    "firmware_sha256": build.get("firmware_hash"), "firmware_hash_valid": firmware_valid,
                    "health_failures":sum(not sample["passed"] for sample in samples),
                    "health_p95_ms":timings[min(len(timings)-1, int(len(timings)*.95))] if timings else None}
                (evidence / "health.json").write_text(json.dumps(samples, indent=2))
                (evidence / "result.json").write_text(json.dumps(report, indent=2))
                print(json.dumps(report), flush=True)
                return 0 if passed else 1
    except Exception as error:
        (evidence / "qualification-error.json").write_text(json.dumps({
            "passed":False, "error_type":type(error).__name__,
            "code":getattr(error, "code", "QUALIFICATION_INFRASTRUCTURE_FAILED"),
            "run_id":current.get("run_id") if current else None,
            "health_samples":len(samples)}, indent=2))
        if current:
            (evidence / "last-run.json").write_text(json.dumps(current, indent=2), encoding="utf-8")
        (evidence / "health.json").write_text(json.dumps(samples, indent=2))
        raise
    finally:
        if job:
            job.close()
        if "child" in locals():
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
