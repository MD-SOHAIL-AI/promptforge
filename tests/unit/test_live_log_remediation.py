"""Regressions for the October 9 evening intake and phase failures."""
import asyncio
import time

import pytest

from backend.agent_runtime.intake import AgentIntakeStore
from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.autonomous_loop import _await_before_deadline, _OperationDeadlineExpired
from backend.agent_runtime.failure_contract import failure_details
from backend.model_router.run_budget import charge_attempt, AgentBudgetPersistenceError
from backend.model_router.models import ModelRequest


def test_citation_splitting_preserves_coverage_and_strict_bounds():
    from backend.agent_runtime.requirement_arguments import split_requirement_spans, validate_requirement_checks, RequirementArgumentError
    checks=[{"id":"req-1","status":"implemented","evidence":[{"path":"main.cpp","start_line":7,"end_line":167}]}]
    normalized=split_requirement_spans(checks)
    assert normalized[0]["evidence"] == [{"path":"main.cpp","start_line":7,"end_line":86},
        {"path":"main.cpp","start_line":87,"end_line":166},{"path":"main.cpp","start_line":167,"end_line":167}]
    assert checks[0]["evidence"][0]["end_line"] == 167
    validate_requirement_checks(normalized)
    for start,end in [(True,81),(0,81),(1,641),(10,2),(1,10000001)]:
        invalid=[{**checks[0],"evidence":[{"path":"main.cpp","start_line":start,"end_line":end}]}]
        with pytest.raises(RequirementArgumentError):
            validate_requirement_checks(split_requirement_spans(invalid))


def test_local_include_readiness_lists_missing_modules_and_interface_pairs():
    from backend.agent_runtime.build_readiness import local_header_readiness
    files = {"src/main.cpp": '#include "sensors/DHTSensor.h"\n#include "network/WiFiManager.h"\n#include <Arduino.h>\n',
             "src/control/Relay.h": "class Relay { public: void begin(int pin); };\n",
             "src/control/Relay.cpp": "void Relay::begin() {}\n"}
    result = local_header_readiness(files, files.__getitem__)
    assert not result["ready"]
    assert {item["path"] for item in result["missing_local_headers"]} == {"src/sensors/DHTSensor.h", "src/network/WiFiManager.h"}
    assert result["interface_pairs"][0]["implementations"] == ["src/control/Relay.cpp"]
    assert "void begin(int pin)" in result["interfaces"][0]["declaration"]


@pytest.mark.parametrize("old,count", [("missing", 0), ("same", 2)])
def test_ambiguous_edits_require_a_fresh_read_and_preserve_source(tmp_path, old, count):
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor, EditMatchError
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.agent_runtime.tool_contracts import ToolCall, ToolName
    (tmp_path / "src").mkdir()
    target = tmp_path / "src/main.cpp"
    target.write_bytes(("same\nsame\n" + "int x;\n" * 170).encode())
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    before = target.read_text()
    with pytest.raises(EditMatchError) as caught:
        executor.execute(ToolCall(ToolName.EDIT_FILE_SIMPLE, {"path":"src/main.cpp", "old_text":old,"new_text":"new"}))
    assert caught.value.details["match_count"] == count and target.read_text() == before
    unique = ToolCall(ToolName.EDIT_FILE_SIMPLE, {"path":"src/main.cpp","old_text":"same\nsame","new_text":"unique"})
    with pytest.raises(EditMatchError):
        executor.execute(unique)
    read = executor.execute(ToolCall(ToolName.READ_FILE, {"path":"src/main.cpp", "limit":200}))
    assert read["citation_ranges"] == [{"start_line":1,"end_line":80},{"start_line":81,"end_line":160},{"start_line":161,"end_line":172}]
    assert executor.execute(unique)["modified"]


def test_exact_dependency_version_is_rejected_before_compilation(tmp_path, monkeypatch):
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_platformio_service import FakeSubprocessManager, process_result
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nlib_deps=me-no-dev/AsyncTCP@1.2.3\n")
    service = PlatformIOService(FakeSubprocessManager(
        process_result(returncode=1,stderr="UnknownPackageError: Could not find the package with 1.2.3 requirements"),
        process_result(stdout="Compatible Platforms  espressif32\nCompatible Frameworks  arduino\nVersion  Size  Published\n1.1.1  13KB  2024-01-01\n")))
    async def search(*args, **kwargs): return ("me-no-dev/AsyncTCP",)
    monkeypatch.setattr(service,"search_libraries",search)
    result = asyncio.run(service.validate_dependencies(tmp_path))
    assert not result["valid"] and not result["versions_verified"]
    assert result["missing"][0]["reason"] == "version_unresolved"
    assert result["missing"][0]["available_versions"] == ["1.1.1"]


def test_dependency_preflight_uses_selected_environment_and_registry_compatibility(tmp_path, monkeypatch):
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_platformio_service import FakeSubprocessManager, process_result
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nlib_deps=owner/Library@1.0.0\n[env:native]\nplatform=native\nlib_deps=unrelated/Other\n")
    service=PlatformIOService(FakeSubprocessManager(process_result(stdout="Compatible Platforms  atmelavr\nCompatible Frameworks  arduino\n")))
    async def search(*args, **kwargs):return ("owner/Library",)
    monkeypatch.setattr(service,"search_libraries",search)
    result=asyncio.run(service.validate_dependencies(tmp_path,environment="esp32dev"))
    assert result["environments"] == ["esp32dev"] and not result["valid"]
    assert result["missing"][0]["reason"] == "environment_incompatible"


def test_resume_rebuilds_historical_success_before_requesting_citations(tmp_path):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("void setup() {}\nvoid loop() {}\n")
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    builder=Builder()
    class Planner(SequencePlanner):
        supports_durable_continuation = True
        def request_turn(self, **kwargs):
            assert builder.calls == 1
            assert kwargs["tool_results"][-1]["call_id"] == "resume_validate_build"
            return super().request_turn(**kwargs)
    async def progress(*args):pass
    loop=AutonomousForgeAgentLoop(workspace_root=tmp_path,planner=Planner([plan(final=True)]),
        platformio=builder,progress=progress,policy=BUILD_REPAIR_PROFILE.policy(),trusted_project=True,
        resume_checkpoint={"observations":[{"tool":"build_firmware","status":"completed","output":{"success":True}}]})
    result=asyncio.run(loop.run(task="continue the saved project",require_build=True))
    assert result.build_verified and builder.calls == 1


def test_risk_review_repairs_invalid_output_and_records_physical_attempts(tmp_path):
    from backend.agent_runtime.risk_review import review_changes
    from backend.model_router.models import ModelResponse
    from backend.model_router.run_budget import settle_attempt
    jobs = AgentJobStore(AgentOrchestrationStore(tmp_path / "state.sqlite3"))
    job = jobs.create(project_id="p",session_id="s",workspace_root=tmp_path,run_id="r")
    class Router:
        requests = []
        async def generate_model(self, request):
            self.requests.append(request)
            identity = f"risk-{len(self.requests)}"
            request.execution_context.diagnostics["physical_attempt_id"] = identity
            charge_attempt(request,attempt_id=identity)
            settle_attempt(request,30,complete=True)
            return ModelResponse(content="{broken" if len(self.requests)==1 else '{"verdict":"approve","summary":"Reviewed diff."}',provider_id="nvidia_nim",model_id="selected")
    router = Router()
    result = asyncio.run(review_changes(router,jobs,{"routing":{"job_id":job["job_id"],"run_id":"r","provider_id":"nvidia_nim","model_id":"selected"}}))
    assert result["verdict"] == "approve" and result["attempts"] == 2
    assert [request.max_tokens for request in router.requests] == [2048,4096]
    assert all(request.provider_id == "nvidia_nim" and not request.allow_fallback for request in router.requests)
    assert jobs.attempt_accounting(job["job_id"])["physical_attempts"] == 2


@pytest.mark.parametrize("content,finish,code", [("",None,"RISK_REVIEW_EMPTY"),("{}",None,"RISK_REVIEW_INVALID"),('{"verdict":"approve","summary":"fine"}',"length","RISK_REVIEW_TRUNCATED")])
def test_risk_review_failure_is_specific_and_never_approves(tmp_path,content,finish,code):
    from backend.agent_runtime.risk_review import review_changes
    from backend.model_router.models import ModelResponse
    jobs=AgentJobStore(AgentOrchestrationStore(tmp_path / "state.sqlite3"))
    job=jobs.create(project_id="p",session_id="s",workspace_root=tmp_path,run_id="r")
    class Router:
        async def generate_model(self, request):
            return ModelResponse(content=content,provider_id="nvidia_nim",model_id="selected",finish_reason=finish)
    result=asyncio.run(review_changes(Router(),jobs,{"routing":{"job_id":job["job_id"],"run_id":"r"}}))
    assert result["verdict"] == "block" and result["status"] == "needs_review"
    assert result["failure_code"] == code and result["attempts"] == 2


@pytest.mark.parametrize("code", ["MODEL_OUTPUT_EMPTY", "MODEL_OUTPUT_TRUNCATED", "PROVIDER_CONTENT_MISSING"])
def test_risk_review_repairs_adapter_normalization_failure_once(tmp_path, code):
    from backend.agent_runtime.risk_review import review_changes
    from backend.model_router.models import ModelResponse
    from backend.model_router.normalization import ProviderNormalizationError
    from backend.model_router.run_budget import settle_attempt
    jobs = AgentJobStore(AgentOrchestrationStore(tmp_path / "state.sqlite3"))
    job = jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    class Router:
        requests = []
        async def generate_model(self, request):
            self.requests.append(request)
            charge_attempt(request, attempt_id=f"risk-{len(self.requests)}")
            settle_attempt(request, 30, complete=True)
            if len(self.requests) == 1:
                raise ProviderNormalizationError(code, "Adapter rejected the response.")
            return ModelResponse(content='{"verdict":"approve","summary":"Complete review."}',
                                 provider_id="nvidia_nim", model_id="selected")
    router = Router()
    result = asyncio.run(review_changes(router, jobs, {"routing": {"job_id": job["job_id"], "run_id": "r",
                                         "provider_id": "nvidia_nim", "model_id": "selected"}}))
    assert result["verdict"] == "approve" and result["attempts"] == 2
    assert [request.max_tokens for request in router.requests] == [2048, 4096]
    assert jobs.attempt_accounting(job["job_id"])["physical_attempts"] == 2


def test_intake_physical_attempt_survives_reopen_settlement_and_job_attachment(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    jobs = AgentJobStore(store)
    intakes = AgentIntakeStore(store)
    intake = intakes.create("s", "p", "interpret this request")
    charges = {"requests": 1, "output_tokens": 2048, "input_bytes": 100, "_attempt_id": "physical-1"}
    assert intakes.reserve(intake["intake_id"], charges)["requests"] == 1
    reopened = AgentIntakeStore(AgentOrchestrationStore(store.path))
    assert reopened.reserve(intake["intake_id"], charges)["requests"] == 1
    assert reopened.settle(intake["intake_id"], "physical-1", 25)["output_tokens"] == 25
    assert reopened.settle(intake["intake_id"], "physical-1", 25)["output_tokens"] == 25
    reopened.finish(intake["intake_id"], "ready", 2)
    job = jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="run")
    attached = reopened.attach(intake["intake_id"], job["job_id"])
    assert attached["consumed"]["requests"] == 1
    assert attached["consumed"]["elapsed_seconds"] == 2
    assert reopened.attach(intake["intake_id"], job["job_id"])["consumed"] == attached["consumed"]
    assert jobs.attempt_accounting(job["job_id"]) == {
        "reserved_output_tokens": 2048, "actual_completed_output_tokens": 25,
        "unresolved_output_tokens": 0, "physical_attempts": 1}


@pytest.mark.parametrize("invalid", [{"extra": 1}, {"requests": True}, {"input_bytes": -1}, {"_attempt_id": []}])
def test_invalid_intake_charges_are_atomic(tmp_path, invalid):
    intake_store = AgentIntakeStore(AgentOrchestrationStore(tmp_path / "state.sqlite3"))
    intake = intake_store.create("s", "p", "request")
    charges = {"requests": 1, "output_tokens": 20, "input_bytes": 100, "_attempt_id": "a"}
    with pytest.raises(ValueError, match="AGENT_BUDGET_CHARGE_INVALID"):
        intake_store.reserve(intake["intake_id"], {**charges, **invalid})
    assert intake_store.reserve(intake["intake_id"], charges)["requests"] == 1
    with pytest.raises(ValueError, match="AGENT_ATTEMPT_CONFLICT"):
        intake_store.reserve(intake["intake_id"], {**charges, "input_bytes": 101})


def test_budget_persistence_failure_is_safe_and_prevents_a_charge():
    def reject(_charges):
        raise ValueError("AGENT_BUDGET_CHARGE_INVALID")
    budget = {"requests": 0, "output_tokens": 0, "input_bytes": 0, "_reserve": reject}
    request = ModelRequest(prompt="request", metadata={"forgex_run_budget": budget})
    with pytest.raises(AgentBudgetPersistenceError) as caught:
        charge_attempt(request, attempt_id="physical")
    assert caught.value.details["cause_code"] == "AGENT_BUDGET_CHARGE_INVALID"
    assert budget["requests"] == 0


@pytest.mark.parametrize("phase,code", [("build", "BUILD_PHASE_TIMEOUT"), ("dependencies", "DEPENDENCY_PHASE_TIMEOUT")])
def test_phase_timeout_keeps_parent_budget_and_settles_cancellation(phase, code):
    stopped = []
    async def work():
        try:
            await asyncio.sleep(30)
        finally:
            stopped.append(True)
    async def scenario():
        with pytest.raises(_OperationDeadlineExpired) as caught:
            await _await_before_deadline(work(), time.monotonic() + .01,
                phase=phase, parent_deadline=time.monotonic() + 900)
        assert stopped == [True]
        assert caught.value.code == code
        assert caught.value.details["remaining_job_seconds"] > 890
        report = failure_details(code, status="failed", evidence={"budget": caught.value.details})
        assert report["category"] == "execution_deadline"
        assert report["normalized_code"] == code
    asyncio.run(scenario())


def test_parent_deadline_remains_job_exhaustion():
    async def scenario():
        deadline = time.monotonic() + .01
        with pytest.raises(_OperationDeadlineExpired) as caught:
            await _await_before_deadline(asyncio.sleep(30), deadline, phase="build", parent_deadline=deadline)
        assert caught.value.code == "AGENT_RUNTIME_LIMIT_EXCEEDED"
        assert caught.value.details["origin"] == "job_deadline"
    asyncio.run(scenario())
