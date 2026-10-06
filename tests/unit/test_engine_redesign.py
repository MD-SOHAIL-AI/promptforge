import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import struct
import time
from types import SimpleNamespace

import pytest

from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.task_contract import create_task_contract, verify_requirement_evidence, acceptance_report
from backend.agent_runtime.tool_contracts import ProductToolPlan
from backend.agent_runtime.verification import settle_review, review_requirements, reviewed_current
from backend.cli import EngineClient, MODES
from backend.model_router.run_budget import new_budget, charge_attempt, AgentBudgetExceeded
from backend.services.firmware_manifest import seal_upload_set, seal_platformio_build, verify_upload_set
from backend.services.firmware_manifest import lock_upload_set
from backend.services.flash_service import ProjectFlashService, FlashServiceError
from backend.services.project_profile import inspect_project_profile
from backend.services.esp_idf_adapter import EspIdfAdapter
from backend.tools.board_detector import BoardType
from tests.unit.test_flash_firmware import artifact, board, install_ports, FakeSubprocessManager, process_result, run_flash


def sealed(tmp_path, chip="esp32"):
    images = []
    for role, offset, data in (("bootloader", 0x1000, b"boot"), ("partitions", 0x8000, b"partitions"), ("application", 0x10000, b"application")):
        path = tmp_path / (role + ".bin")
        path.write_bytes(data)
        images.append((role, offset, path))
    return seal_upload_set(tmp_path / "store", chip=chip, board="esp32dev", environment="esp32dev", images=images)


@pytest.mark.parametrize("role", ["bootloader", "partitions", "application"])
def test_every_approved_upload_image_is_checked(tmp_path, role):
    manifest = sealed(tmp_path)
    verify_upload_set(manifest, store=tmp_path / "store")
    (Path(manifest["artifact_root"]) / (role + ".bin")).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SEALED_ARTIFACT_TAMPERED"):
        verify_upload_set(manifest)


def test_source_changes_cannot_change_sealed_upload(tmp_path):
    manifest = sealed(tmp_path)
    (tmp_path / "application.bin").write_bytes(b"rebuilt")
    assert (Path(manifest["artifact_root"]) / "application.bin").read_bytes() == b"application"
    verify_upload_set(manifest)
    manifest["images"][0]["offset"] = 0
    with pytest.raises(ValueError, match="MANIFEST_TAMPERED"):
        verify_upload_set(manifest)


@pytest.mark.skipif(__import__("os").name != "nt", reason="Windows upload sharing locks")
def test_approved_images_cannot_change_while_uploader_reads_them(tmp_path):
    manifest = sealed(tmp_path)
    application = Path(manifest["artifact_root"]) / "application.bin"
    with lock_upload_set(manifest):
        assert application.read_bytes() == b"application"
        with pytest.raises(OSError):
            application.write_bytes(b"raced")
        with pytest.raises(OSError):
            application.unlink()
    application.write_bytes(b"raced")
    with pytest.raises(ValueError, match="TAMPERED"):
        verify_upload_set(manifest)


def test_sealed_flash_never_invokes_platformio(tmp_path, monkeypatch):
    manifest = sealed(tmp_path)
    image = replace(artifact(tmp_path / "unused.bin"), path=Path(manifest["artifact_root"]) / "application.bin",
                    environment="esp32dev", size_bytes=len(b"application"), manifest=manifest)
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(process_result(stdout="esptool v5.4.0"), process_result(stdout="Hash of data verified."))
    result = run_flash(manager, image, board(BoardType.ESP32))
    assert result.success
    command = manager.calls[-1].args
    assert "write-flash" in command and "pio" not in command and "upload" not in command
    for offset, path in verify_upload_set(manifest):
        assert hex(offset) in command and str(path) in command


def test_approved_flash_service_does_not_rebuild(tmp_path):
    manifest = sealed(tmp_path)
    class NoBuild:
        async def build(self, *args, **kwargs):
            raise AssertionError("Approval must not trigger a build")
    captured = []
    def execute(name, image, device, config, manager):
        from backend.runtime.result import FlashResult, ResultStatus
        captured.append(image)
        return FlashResult(success=True, status=ResultStatus.SUCCESS)
    service = ProjectFlashService(platformio=NoBuild(), detector=SimpleNamespace(detect_boards=lambda: [board(BoardType.ESP32)]),
        manager=object(), tool_executor=execute, artifact_store=tmp_path / "store")
    outcome = asyncio.run(service.build_and_flash(project_id="p", project_path=tmp_path, board_type=BoardType.ESP32,
        port="COM7", environment="esp32dev", expected_artifact_hash=manifest["images"][-1]["sha256"], artifact_manifest=manifest))
    assert outcome.flash.success and captured[0].manifest == manifest
    with pytest.raises(FlashServiceError, match="SEALED_UPLOAD|validation"):
        asyncio.run(service.build_and_flash(project_id="p", project_path=tmp_path, board_type=BoardType.ESP32,
            port="COM7", expected_artifact_hash="0" * 64))


def test_partition_manifest_uses_actual_application_partition(tmp_path):
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    output = tmp_path / ".pio/build/esp32dev"
    output.mkdir(parents=True)
    (output / "bootloader.bin").write_bytes(b"boot")
    partition = struct.pack("<HBBII16sI", 0x50AA, 0, 0, 0x20000, 0x100000, b"factory", 0) + b"\xff" * 32
    (output / "partitions.bin").write_bytes(partition)
    (output / "firmware.bin").write_bytes(b"firmware")
    result = SimpleNamespace(firmware_path=output / "firmware.bin", metadata={})
    manifest = seal_platformio_build(result, tmp_path, "esp32dev", tmp_path / "store")
    assert manifest["images"][-1]["offset"] == 0x20000


def test_unrelated_quote_is_referenced_but_never_verified(tmp_path):
    (tmp_path / "main.cpp").write_text("int unrelated = 42;")
    contract = create_task_contract("Debounce the input for 200 ms", project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    report = verify_requirement_evidence(contract, [{"id": "req-1", "status": "implemented",
        "evidence": [{"path": "main.cpp", "quote": "int unrelated = 42;"}]}], tmp_path, "r1")
    assert report["references_valid"] and not report["valid"]
    reviewer = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "final": True, "tool_calls": [],
        "summary": json.dumps({"checks": [{"id": "req-1", "verdict": "unverified", "reason": "No debounce logic exists."}]})})
    result = settle_review(contract, report, reviewer, "r1")
    assert not result["valid"] and not reviewed_current(result, "r1")
    assert not acceptance_report(contract, {"valid": True, "workspace_revision": "r1"}, {}, [], "r1")["passed"]


@pytest.mark.parametrize("checks", [[], [{"id": [], "verdict": "satisfied", "reason": "x"}],
    [{"id": "req-1", "verdict": "satisfied", "reason": "x"}] * 2])
def test_malformed_review_fails_closed(tmp_path, checks):
    contract = {"requirements": [{"id": "req-1"}]}
    report = {"workspace_revision": "r1", "checks": [{"id":"req-1"}], "references_valid": True}
    plan = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "final": True, "tool_calls": [], "summary": json.dumps({"checks": checks})})
    with pytest.raises(ValueError):
        settle_review(contract, report, plan, "r1")


def test_reviewer_cancellation_propagates():
    class Planner:
        async def request_turn_async(self, **kwargs):
            raise asyncio.CancelledError()
    contract = {"requirements": [{"id": "r", "text": "x"}]}
    report = {"references_valid": True, "checks": [], "workspace_revision": "r1"}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(review_requirements(Planner(), contract, report, lambda path: "", "r1", deadline=time.monotonic() + 10, run_id="run"))


def test_parallel_parent_and_reviewer_share_atomic_persistent_budget(tmp_path):
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "jobs.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    owner.mutate(job["job_id"], lambda value: value["limits"].update(requests=5))
    def request(index):
        budget = new_budget()
        budget["_reserve"] = lambda charges: owner.reserve_provider_attempt(job["job_id"], "r", charges)
        try:
            charge_attempt(SimpleNamespace(metadata={"forgex_run_budget": budget}, prompt="test", system_prompt=None, max_tokens=10))
            return True
        except AgentBudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request, range(12)))
    assert sum(results) == 5
    assert owner.get(job["job_id"])["consumed"]["requests"] == 5
    assert owner.get(job["job_id"])["consumed"]["output_tokens"] == 50


def test_paused_job_cannot_issue_review_requests(tmp_path):
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "jobs.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    owner.mutate(job["job_id"], lambda value: value.update(status="paused", paused_by="user"))
    with pytest.raises(ValueError, match="NOT_RUNNING"):
        owner.reserve_provider_attempt(job["job_id"], "r", {"requests": 1, "output_tokens": 1, "input_bytes": 1})


def test_profile_resolves_inheritance_and_requires_environment_selection(tmp_path):
    (tmp_path / "platformio.ini").write_text("[env]\nframework=arduino\nplatform=espressif32\n[env:base]\nboard=esp32dev\n[env:release]\nextends=base\nlib_deps=owner/library@1.0\n")
    ambiguous = inspect_project_profile(tmp_path)
    assert ambiguous.selection_required and ambiguous.selected_environment is None
    selected = inspect_project_profile(tmp_path, environment="release")
    assert selected.environments[-1].board == "esp32dev"
    assert selected.environments[-1].dependencies == ["owner/library@1.0"]
    with pytest.raises(ValueError, match="ENVIRONMENT_NOT_FOUND"):
        inspect_project_profile(tmp_path, environment="invented")


def test_profile_uses_only_selected_environment_compiler_commands(tmp_path):
    (tmp_path / "platformio.ini").write_text("[env:a]\nboard=esp32dev\n[env:b]\nboard=esp32dev\n")
    source = tmp_path / "main.cpp"
    source.write_text("int x;")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"file": str(source), "directory": str(tmp_path), "arguments": ["compiler", "-DA=1", "-I", "include", "-o", ".pio/build/a/main.o"]},
        {"file": str(source), "directory": str(tmp_path), "arguments": ["compiler", "-DB=1", "-o", ".pio/build/b/main.o"]}]))
    profile = inspect_project_profile(tmp_path, environment="a")
    assert profile.compiler_context_available and len(profile.compilation_units) == 1
    assert profile.compilation_units[0].defines == ["A=1"]


def test_cli_attaches_only_to_authenticated_loopback_and_review_is_staged():
    assert MODES["review"] == "review_changes"
    for url in ("https://example.com", "http://127.0.0.1.evil.test:8000", "http://user@localhost:8000"):
        with pytest.raises(ValueError):
            EngineClient(url, "token")
    with pytest.raises(ValueError, match="TOKEN"):
        EngineClient("http://127.0.0.1:8000", None)


def test_cli_flash_binding_is_explicit_and_never_guessed():
    client = EngineClient("http://127.0.0.1:8000", "token")
    calls = []
    def request(method, path, body=None):
        calls.append((method, path, body))
        return {"run": {"status": "awaiting_flash_confirmation", "approval_id": "approval", "flash_port": "COM7",
                        "build_result": {"artifact_manifest": {"sha256": "digest"}}}}
    client.request = request
    with pytest.raises(ValueError, match="BINDING_MISMATCH"):
        client.confirm_flash("r", approval_id="approval", manifest_sha256="changed", port="COM7")
    assert len(calls) == 1 and calls[0][0] == "GET"
    client.confirm_flash("r", approval_id="approval", manifest_sha256="digest", port="COM7")
    assert calls[-1] == ("POST", "/agent-runtime/runs/r/confirm-flash", {"port": "COM7"})


def test_cli_follows_a_recovered_job_without_resubmitting(tmp_path, monkeypatch):
    client = EngineClient("http://127.0.0.1:8000", "token")
    calls = []
    first = {"run_id":"r1", "job_id":"j", "status":"paused_model_unavailable"}
    replacement = {"run_id":"r2", "job_id":"j", "status":"completed"}
    def request(method, path, body=None):
        calls.append((method, path))
        return {"/projects/import":{"project_id":"p"}, "/agent-runtime/sessions":{"session_id":"s"},
            "/agent-runtime/sessions/s/messages":{"run":first},
            "/agent-runtime/jobs/j":{"job":{"current_run_id":"r2", "status":"completed"}},
            "/agent-runtime/runs/r2":{"run":replacement}}[path]
    client.request = request
    monkeypatch.setattr("backend.cli.time.sleep", lambda delay: None)
    assert client.run(tmp_path, "Inspect the firmware") == replacement
    assert calls.count(("POST", "/agent-runtime/sessions/s/messages")) == 1


def test_cli_stop_cancels_the_session_during_job_recovery(tmp_path, monkeypatch):
    client = EngineClient("http://127.0.0.1:8000", "token")
    calls = []
    def request(method, path, body=None):
        calls.append((method, path))
        return {"/projects/import":{"project_id":"p"}, "/agent-runtime/sessions":{"session_id":"s"},
            "/agent-runtime/sessions/s/messages":{"run":{"run_id":"r1","job_id":"j","status":"paused_model_unavailable"}},
            "/agent-runtime/jobs/j":{"job":{"current_run_id":"r1","recovery_policy":"safe_auto","retry_due_at":"soon"}},
            "/agent-runtime/sessions/s/cancel":{}}[path]
    client.request = request
    def stop(delay):
        raise KeyboardInterrupt()
    monkeypatch.setattr("backend.cli.time.sleep", stop)
    with pytest.raises(KeyboardInterrupt):
        client.run(tmp_path, "Inspect the firmware")
    assert calls[-1] == ("POST", "/agent-runtime/sessions/s/cancel")


def idf_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "CMakeLists.txt").write_text("project(example)")
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32"\n')
    return project


def test_native_idf_requires_activation_trust_and_a_host_sandbox(tmp_path):
    project = idf_project(tmp_path)
    adapter = EspIdfAdapter(idf_root=tmp_path / "sdk", artifact_store=tmp_path / "store")
    with pytest.raises(ValueError, match="EXPERIMENTAL_DISABLED"):
        asyncio.run(adapter.build(project, trusted=True))
    adapter.enabled = True
    with pytest.raises(ValueError, match="TRUST_REQUIRED"):
        asyncio.run(adapter.build(project))
    with pytest.raises(ValueError, match="SANDBOX_REQUIRED"):
        asyncio.run(adapter.build(project, trusted=True))


def test_native_idf_uses_sdk_offsets_and_rejects_unknown_images(tmp_path):
    project = idf_project(tmp_path)
    build = project / "build"
    build.mkdir()
    files = {"0x1000":"bootloader.bin", "0x8000":"partitions.bin", "0x20000":"app.bin"}
    for name in files.values():
        (build / name).write_bytes(name.encode())
    metadata = {"extra_esptool_args":{"chip":"esp32"}, "flash_files":files,
        "bootloader":{"file":"bootloader.bin"}, "partition_table":{"file":"partitions.bin"}, "app":{"file":"app.bin"}}
    path = build / "flasher_args.json"
    path.write_text(json.dumps(metadata))
    adapter = EspIdfAdapter(idf_root=tmp_path / "sdk", artifact_store=tmp_path / "store")
    manifest = adapter.seal_build(project)
    assert manifest["images"][-1]["offset"] == 0x20000
    assert verify_upload_set(manifest, environment="esp-idf")
    metadata["flash_files"]["0x30000"] = "invented.bin"
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="IMAGE_UNSUPPORTED"):
        adapter.seal_build(project)


def test_generated_engine_contracts_are_current(tmp_path, monkeypatch):
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("engine_generator", root / "scripts/generate-engine-contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / "docs").mkdir()
    (tmp_path / "frontend/types").mkdir(parents=True)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    module.generate()
    for relative in ("docs/engine-contracts.schema.json", "frontend/types/engine-contracts.ts",
                     "docs/runtime-policy.generated.json", "docs/runtime-policy.generated.md", "frontend/lib/runtime-policy.generated.ts"):
        assert (tmp_path / relative).read_text(encoding="utf-8") == (root / relative).read_text(encoding="utf-8")


def test_public_tool_catalog_uses_actual_provider_schemas():
    from backend.agent_runtime.engine_tools import engine_tool_catalog
    from backend.agent_runtime.tool_contracts import ToolName, provider_function_schema
    catalog = engine_tool_catalog()
    assert catalog.execution_authority == "python"
    assert {tool.name for tool in catalog.tools} == set(ToolName)
    for tool in catalog.tools:
        assert tool.arguments == provider_function_schema(tool.name)["function"]["parameters"]
        assert tool.provider_name == provider_function_schema(tool.name)["function"]["name"]
    assert not any(tool.stages_files for tool in catalog.tools if "ask" in tool.capability_profiles)


def test_symbol_inspection_never_uses_another_environment_database(tmp_path):
    from backend.agent_runtime.esp32_tools import local_tool
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor
    from backend.agent_runtime.tool_contracts import ToolName
    from backend.agent_runtime.tool_policy import product_agent_policy
    (tmp_path / "platformio.ini").write_text("[env:a]\nboard=esp32dev\n[env:b]\nboard=esp32dev\n")
    (tmp_path / "main.cpp").write_text("int sensorValue = 1;")
    (tmp_path / "compile_commands.json").write_text(json.dumps([
        {"directory":str(tmp_path),"file":"main.cpp","arguments":["compiler","-o",".pio/build/a/main.o"]}]))
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy(), environment="b")
    result = local_tool(executor, ToolName.INSPECT_SYMBOLS, {"symbol":"sensorValue"})
    assert result["lexical_only"] and not result["matches"][0]["compiled"]
    executor.environment = "a"
    assert local_tool(executor, ToolName.INSPECT_SYMBOLS, {"symbol":"sensorValue"})["matches"][0]["compiled"]


def test_release_upload_wheel_is_bound_to_its_source_lock(tmp_path, monkeypatch):
    import importlib.util
    import hashlib
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("release_prepare", root / "scripts/prepare-release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lock = tmp_path / "source.lock"
    lock.write_text("esptool==5.4.0 \\\n    --hash=sha256:" + "a" * 64 + "\nother==1 --hash=sha256:" + "b" * 64 + "\n")
    arguments = []
    def wheel(args):
        arguments.extend(args)
        (Path(args[-1]) / "esptool-5.4.0-py3-none-any.whl").write_bytes(b"wheel")
    monkeypatch.setattr(module, "run", wheel)
    resolved, wheels = module.prepare_upload_tool_wheel("python", lock, tmp_path)
    assert "--require-hashes" in arguments and "--no-deps" in arguments
    assert (tmp_path / "esptool-source.lock").read_text().count("--hash=sha256:") == 1
    assert hashlib.sha256(b"wheel").hexdigest() in resolved.read_text()
    assert "other==1" in resolved.read_text()
