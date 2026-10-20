"""Regression coverage for the live Windows registry-printing failure."""
import asyncio
import json
import sys
import time

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager
from backend.services.platformio_service import PlatformIODependencyError, PlatformIOService
from tests.unit.test_platformio_service import FakeSubprocessManager, process_result


def test_python_tool_output_is_utf8_despite_host_cp1252(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", "secret-must-not-reach-tool")

    async def scenario():
        result = await SubprocessManager().run(ProcessConfig(args=[sys.executable, "-c",
            "import json,os; print(json.dumps({'star':'\\u2b50','secret_present':'FORGEX_LOCAL_TOKEN' in os.environ}, ensure_ascii=False))"]))
        assert result.success, result.stderr_text
        assert json.loads(result.stdout_text) == {"star": "\u2b50", "secret_present": False}
    asyncio.run(scenario())


@pytest.mark.parametrize("stderr,timed_out,code,retryable", [
    ("UnicodeEncodeError: 'charmap' codec can't encode character '\\u2b50'", False, "DEPENDENCY_OUTPUT_ENCODING_ERROR", False),
    ("network unavailable", False, "DEPENDENCY_REGISTRY_UNAVAILABLE", True),
    ("ReadTimeout: registry timed out", True, "DEPENDENCY_REGISTRY_TIMEOUT", True),
    ("ModuleNotFoundError: missing_dependency", False, "DEPENDENCY_VALIDATION_FAILED", False),
])
def test_registry_failure_retains_real_cause_and_retryability(monkeypatch, stderr, timed_out, code, retryable):
    events = []
    monkeypatch.setattr("backend.observability.diagnostics.emit_diagnostic_event", lambda **fields: events.append(fields))
    service = PlatformIOService(FakeSubprocessManager(process_result(
        returncode=1, stderr=stderr, timed_out=timed_out)))
    with pytest.raises(PlatformIODependencyError) as captured:
        asyncio.run(service.search_libraries("ArduinoJson"))
    error = captured.value
    assert error.code == code and error.retryable is retryable
    assert error.details["query"] == "ArduinoJson"
    assert error.details["diagnostics"] == stderr
    assert error.details["exit_code"] == 1
    assert events[0]["error_code"] == code and events[0]["diagnostics"] == stderr


def test_dependency_diagnostics_are_redacted_and_bounded():
    error = PlatformIODependencyError("ArduinoJson", process_result(
        returncode=1, stderr="x" * 12000 + "\nUnicodeEncodeError password=hidden-value Bearer abc123"))
    assert error.code == "DEPENDENCY_OUTPUT_ENCODING_ERROR"
    assert "hidden-value" not in error.details["diagnostics"]
    assert "abc123" not in error.details["diagnostics"]
    assert len(error.details["diagnostics"]) <= 8000


@pytest.mark.parametrize("kind", ["encoding", "unknown_exception"])
def test_local_validation_errors_stop_without_source_repair_or_outage_retry(tmp_path, kind):
    class FailedValidation:
        checks = 0
        async def validate_dependencies(self, root):
            self.checks += 1
            if kind == "encoding":
                raise PlatformIODependencyError("ArduinoJson", process_result(returncode=1,
                    stderr="UnicodeEncodeError: cannot encode star"))
            raise ValueError("Malformed dependency manifest")
        async def build(self, *args, **kwargs):
            raise AssertionError("compiler must not run after failed preflight")
    async def scenario():
        validator = FailedValidation()
        events = []
        async def progress(event, fields): events.append((event, fields))
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
            platformio=validator, progress=progress, task_contract={"revision": "r"})
        loop._deadline = time.monotonic() + 30
        result = await loop._build("esp32dev")
        output = result["output"]
        assert validator.checks == 1
        assert output["terminal"] is True and output["retryable"] is False
        assert output["error_code"] == ("DEPENDENCY_OUTPUT_ENCODING_ERROR" if kind == "encoding" else "DEPENDENCY_VALIDATION_FAILED")
        assert "registry validation is unavailable" not in output["message"]
        assert output["diagnostics"]
        assert events[0][1]["error_code"] == output["error_code"]
        assert loop.repair_attempts == 0
    asyncio.run(scenario())


def test_terminal_milestone_preflight_stops_before_another_model_request(tmp_path):
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    class DurablePlanner(SequencePlanner):
        supports_durable_continuation = True
        def request_turn(self, **kwargs):
            assert len(self.seen_results) < 3, "terminal preflight must stop model requests"
            return super().request_turn(**kwargs)
    class BrokenRegistryCommand:
        async def validate_dependencies(self, root):
            raise PlatformIODependencyError("ArduinoJson", process_result(returncode=1,
                stderr="UnicodeEncodeError: cannot encode star"))
        async def build(self, *args, **kwargs):
            raise AssertionError("compiler must not run")
    async def progress(*args): pass
    planner = DurablePlanner([plan(path="src/main.cpp", content="int a;\n", call_id="write_main"),
        plan(path="include/module.h", content="int b;\n", call_id="write_header"),
        plan(path="README.md", content="firmware\n", call_id="write_readme")])
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=planner,
        platformio=BrokenRegistryCommand(), progress=progress, task_contract={"revision": "r"},
        policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True)
    result = asyncio.run(loop.run(task="write and build firmware", require_build=True))
    assert result.classification == "DEPENDENCY_OUTPUT_ENCODING_ERROR"
    assert len(planner.seen_results) == 3 and not result.build_verified


def test_public_dependency_error_uses_the_concrete_build_message():
    from backend.agent_runtime.run_models import ProductAgentRun
    message = "DEPENDENCY_OUTPUT_ENCODING_ERROR: PlatformIO could not encode library-search output for 'ArduinoJson'."
    run = ProductAgentRun(run_id="r", project_id="p", provider_id="nvidia_nim", status="failed",
        classification="DEPENDENCY_OUTPUT_ENCODING_ERROR", build_result={"success": False,
            "error_code": "DEPENDENCY_OUTPUT_ENCODING_ERROR", "message": message, "diagnostics": "UnicodeEncodeError"})
    public = run.to_safe_dict()
    assert public["summary"]["errors"][0]["message"] == message
    assert public["build_result"]["diagnostics"] == "UnicodeEncodeError"
