import asyncio
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import pytest

from backend.agent_runtime.jobs import AgentJobStore, DEFAULT_LIMITS, EXECUTION_PROFILES
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.agent_runtime.tool_contracts import ToolName
from backend.agent_runtime.tool_registry import available_tools, validate_new_arguments
from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.task_contract import acceptance_report, resolve_clarification
from backend.agent_runtime.task_contract import create_task_contract, revise_task_contract
from backend.agent_runtime.task_understanding import validate_interpretation
from backend.changes import ChangeSetService
from tests.unit.test_agent_orchestrator_v2 import _providers


def jobs(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "jobs.sqlite3")
    jobs = AgentJobStore(store)
    job = jobs.create(project_id="p",session_id="s",workspace_root=tmp_path,run_id="r")
    return store,jobs,job


def test_advanced_profile_is_persisted_and_resume_keeps_cumulative_limits(tmp_path):
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "profiles.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path,
                       run_id="first", execution_profile="advanced")
    assert job["limits"] == EXECUTION_PROFILES["advanced"]
    owner.record_elapsed(job["job_id"], 120)
    resumed = owner.attach(job["job_id"], "second")
    assert resumed["consumed"]["elapsed_seconds"] == 120
    assert resumed["limits"] == EXECUTION_PROFILES["advanced"]
    assert owner.public(resumed)["execution_profile"] == "advanced"
    with pytest.raises(ValueError, match="AGENT_EXECUTION_PROFILE_INVALID"):
        owner.create(project_id="p", session_id="s", workspace_root=tmp_path,
                     run_id="invalid", execution_profile="unlimited")


def test_budget_reservations_survive_restart_and_are_idempotent(tmp_path):
    store,owner,job = jobs(tmp_path)
    owner.charge_tool(job["job_id"], "r:call-1")
    owner.charge_tool(job["job_id"], "r:call-1")
    owner.persist_provider_budget(job["job_id"], {"requests":8,"input_bytes":40,"output_tokens":16000})
    reopened = AgentJobStore(AgentOrchestrationStore(store.path))
    assert reopened.get(job["job_id"])["consumed"]["requests"] == 8
    assert reopened.get(job["job_id"])["consumed"]["tool_calls_used"] == 1
    extension = {"requests":120,"elapsed_seconds":900,"tool_calls_used":100}
    first = reopened.extend_budget(job["job_id"], extension,"extend-1")
    second = reopened.extend_budget(job["job_id"], extension,"extend-1")
    assert first["limits"] == second["limits"]
    assert second["limits"]["requests"] == 240
    with pytest.raises(ValueError,match="REQUEST_CONFLICT"):
        reopened.reserve_resume(job["job_id"],"extend-1")


def test_resume_identity_keeps_original_attempt(tmp_path):
    _,owner,job = jobs(tmp_path)
    assert owner.reserve_resume(job["job_id"],"resume-1") == "r"
    owner.attach(job["job_id"],"r2")
    assert owner.reserve_resume(job["job_id"],"resume-1") == "r"


def test_runtime_budget_diagnostics_survive_the_public_run_projection(tmp_path):
    from backend.agent_runtime.tool_runtime import ToolRuntimeResult
    from backend.agent_runtime.tool_contracts import RuntimeClassification
    owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging"),
        provider_registry=_providers(tmp_path), store=AgentOrchestrationStore(tmp_path / "state.sqlite3"),
        run_factory=ProductAgentRun)
    run = ProductAgentRun(run_id="r", project_id="p", provider_id="openai")
    result = ToolRuntimeResult(classification=RuntimeClassification.MODEL_OUTPUT_INVALID,
        provider_id="openai", provider_classification="AGENT_PROVIDER_BUDGET_EXCEEDED",
        provider_diagnostics={"last_failure_code":"AGENT_PROVIDER_BUDGET_EXCEEDED",
            "last_failure_origin":"job_budget", "last_turn":13, "response_mode":"native_tools",
            "physical_request_count":11, "http_status":200, "request_reached_provider":True})
    owner._apply_runtime_result(run, result)
    diagnostics = run.to_safe_dict()["provider_diagnostics"]
    assert diagnostics["last_failure_origin"] == "job_budget"
    assert diagnostics["last_failure_code"] == "AGENT_PROVIDER_BUDGET_EXCEEDED"
    assert diagnostics["last_turn"] == 13


def test_budget_cannot_be_reset_by_checkpoint_or_stale_projection(tmp_path):
    _,owner,job = jobs(tmp_path)
    owner.persist_provider_budget(job["job_id"],{"requests":120,"input_bytes":0,"output_tokens":0})
    value = owner.update_run(job["job_id"],SimpleNamespace(run_id="r",agent_plan=[],classification=None,status="failed"),{})
    assert value["paused_by"] == "budget"
    owner.save(job)
    assert owner.get(job["job_id"])["consumed"]["requests"] == 120
    with pytest.raises(ValueError,match="BUDGET_EXHAUSTED"):
        owner.persist_provider_budget(job["job_id"],{"requests":121,"input_bytes":0,"output_tokens":0})


def test_new_schema_migration_preserves_legacy_data(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    AgentOrchestrationStore(path)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version=2")
        conn.execute("DROP TABLE agent_job_requests")
        conn.execute("CREATE TABLE agent_job_requests(job_id TEXT, request_key TEXT, fingerprint TEXT, PRIMARY KEY(job_id,request_key))")
        conn.commit()
    owner = AgentJobStore(AgentOrchestrationStore(path))
    assert Path(str(path)+".pre-jobs-v3.bak").is_file()
    job = owner.create(project_id="p",session_id="s",workspace_root=tmp_path,run_id="r")
    assert owner.reserve_resume(job["job_id"],"resume-1") == "r"


def test_host_tools_are_only_advertised_with_handlers():
    available = available_tools(BUILD_REPAIR_PROFILE.policy())
    assert "project_doctor" in available
    assert "run_unit_tests" not in available
    assert "observe_serial" not in available
    assert "verify_acceptance" in available_tools(BUILD_REPAIR_PROFILE.policy(),native={ToolName.VERIFY_ACCEPTANCE})
    with pytest.raises(ValueError):
        validate_new_arguments(ToolName.OBSERVE_SERIAL,{"duration_seconds":True})
    with pytest.raises(ValueError):
        validate_new_arguments(ToolName.DECODE_CRASH,{"addresses":["0x12345678; arbitrary command"]})


def test_docs_reject_local_dns_and_redirect_targets(monkeypatch):
    from backend.agent_runtime.esp32_tools import public_doc_target
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))])
    with pytest.raises(ValueError,match="PRIVATE_TARGET"):
        public_doc_target("https://docs.espressif.com/path")
    for url in ("https://localhost/path","https://docs.espressif.com@localhost/path","file:///etc/passwd","https://docs.espressif.com:444/path"):
        with pytest.raises(ValueError):
            public_doc_target(url)


def test_acceptance_does_not_treat_compile_as_test_or_accept_stale_tests():
    contract={"acceptance_checks":[{"id":"unit","kind":"unit_tests","criterion":"Logic tests pass"}]}
    assert not acceptance_report(contract,{}, {"success":True}, [], "r2")["passed"]
    assert not acceptance_report(contract,{}, {},[{"kind":"unit_tests","success":True,"source_revision":"r1"}],"r2")["passed"]
    assert acceptance_report(contract,{}, {},[{"kind":"unit_tests","success":True,"source_revision":"r2"}],"r2")["passed"]


def test_explicit_build_and_simulation_require_current_evidence(tmp_path):
    contract = create_task_contract("Build and simulate the ESP32 firmware.",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert {item["kind"] for item in contract["acceptance_checks"]} == {
        "source_review", "build", "simulation"
    }
    requirements = {"valid": True, "workspace_revision": "r2", "verification_level": "model_review",
                    "semantic_review": {"source_revision": "r2", "task_revision": contract["revision"]}}
    build = {"success": True, "workspace_revision": "r2"}
    assert not acceptance_report(contract, requirements, build, [], "r2")["passed"]
    assert not acceptance_report(contract, requirements, build, [], "r2",
        {"success": True, "workspace_revision": "r1"})["passed"]
    assert acceptance_report(contract, requirements, build, [], "r2",
        {"success": True, "workspace_revision": "r2"})["passed"]


def test_interpretation_rejects_invented_requirements_and_resolves_many_choices():
    request="Use DHT11 or DHT22 or SHT31 for sensing."
    value={"intent":"generate","requirements":[request],"exclusions":[],"choices":[{"id":"sensor","options":["DHT11","DHT22","SHT31"]}]}
    validate_interpretation(value,request)
    resolved,missing=resolve_clarification({"request":request,"choices":value["choices"]},"SHT31")
    assert not missing and "sensor: SHT31" in resolved
    value["requirements"]=["Upload the firmware now"]
    with pytest.raises(ValueError,match="UNGROUNDED"):
        validate_interpretation(value,request)


def test_followup_keeps_requirement_ids_choices_and_exclusions(tmp_path):
    original=create_task_contract("Build an ESP32 LED.\nKeep GPIO25.\nDo not implement smoke sensing.\nConfirmed choices:\ncloud: MQTT",project_id="p",session_id="s",workspace=tmp_path,intent="generate")
    revised=revise_task_contract(original,"Add a button on GPIO18.",project_id="p",session_id="s",workspace=tmp_path,intent="edit")
    assert revised["requirements"][:len(original["requirements"])]==original["requirements"]
    assert revised["parent_revision"]==original["revision"]
    assert revised["revision"]!=original["revision"]
    assert revised["exclusions"]==original["exclusions"]
    assert revised["accepted_choices"]==["cloud: MQTT"]


@pytest.mark.parametrize("output,passed",[("0 test cases: 0 succeeded in 0s",False),("2 test cases: 2 succeeded in 1s",True),("2 test cases: 1 failed, 1 succeeded in 1s",False),("2 test cases: 1 skipped, 1 succeeded in 1s",False),("2 test cases: 2 succeeded in 1s\n0 test cases: 0 succeeded in 0s",False)])
def test_native_test_evidence_requires_executed_passing_tests(tmp_path,monkeypatch,output,passed):
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_platformio_service import FakeSubprocessManager, process_result
    import shutil
    monkeypatch.setattr(shutil,"which",lambda _:"gcc")
    (tmp_path/"platformio.ini").write_text("[env:native]\nplatform = native\n")
    (tmp_path/"src").mkdir()
    (tmp_path/"src/main.cpp").write_text("int main() { return 0; }")
    (tmp_path/"test").mkdir()
    (tmp_path/"test/test_main.cpp").write_text("// fixture")
    manager=FakeSubprocessManager(process_result(stdout=output))
    class IsolatedTestFixture:
        async def run(self, command, *, cwd, timeout_s):
            from backend.runtime.subprocess_mgr import ProcessConfig
            return await manager.run(ProcessConfig(args=command, cwd=cwd, timeout_s=timeout_s))
    service=PlatformIOService(subprocess_mgr=manager,executable="pio",native_test_runner=IsolatedTestFixture())
    result=asyncio.run(service.test_native(tmp_path,environment="native",trusted=True))
    assert result["success"] is passed
    assert manager.calls[0].args==[service.executable,"test","-e","native"]
    with pytest.raises(ValueError,match="TRUST_REQUIRED"):
        asyncio.run(service.test_native(tmp_path,environment="native",trusted=False))


def test_transient_retry_deadline_is_durable_and_honors_retry_after(tmp_path):
    from datetime import datetime,timezone
    store,owner,job=jobs(tmp_path)
    run=SimpleNamespace(run_id="r",agent_plan=[],status="paused_model_unavailable",classification="PROVIDER_RATE_LIMITED",retry_after_ms=120000)
    value=owner.update_run(job["job_id"],run)
    due=datetime.fromisoformat(value["retry_due_at"].replace("Z","+00:00"))
    assert (due-datetime.now(timezone.utc)).total_seconds()>119
    assert AgentJobStore(AgentOrchestrationStore(store.path)).get(job["job_id"])["retry_due_at"]==value["retry_due_at"]
    owner.mutate(job["job_id"],lambda v:v.update(paused_by="user"))
    assert owner.update_run(job["job_id"],run)["paused_by"]=="user"


def test_provider_outage_preserves_time_for_recovery_instead_of_auto_retrying(tmp_path):
    _, owner, job = jobs(tmp_path)
    owner.record_elapsed(job["job_id"], 800)
    run = SimpleNamespace(run_id="r", agent_plan=[], status="paused_model_unavailable",
                          classification="PROVIDER_UNAVAILABLE", retry_after_ms=None)
    saved = owner.update_run(job["job_id"], run)
    assert saved["consumed"]["elapsed_seconds"] == 800
    assert saved["status"] == "paused"
    assert saved["paused_by"] == "budget"
    assert saved["blocking_reason"] == "AGENT_JOB_TIME_RESERVE_REQUIRED"
    assert saved["retry_due_at"] is None
    extended = owner.extend_budget(job["job_id"], {"elapsed_seconds": 900}, "time-extension")
    assert extended["paused_by"] is None
    assert extended["limits"]["elapsed_seconds"] == 1800


def test_generation_reserve_is_distinct_from_exhausting_the_whole_job(tmp_path):
    _, owner, job = jobs(tmp_path)
    owner.record_elapsed(job["job_id"], 600)
    run = SimpleNamespace(run_id="r", agent_plan=[], status="failed",
        classification="AGENT_VERIFICATION_RESERVE_REACHED")
    saved = owner.update_run(job["job_id"], run)
    assert saved["blocking_reason"] == "AGENT_VERIFICATION_RESERVE_REACHED"
    assert saved["limits"]["elapsed_seconds"] - saved["consumed"]["elapsed_seconds"] == 300
    owner.record_elapsed(job["job_id"], 900)
    assert owner.update_run(job["job_id"], run)["blocking_reason"] == "AGENT_JOB_BUDGET_EXHAUSTED"


def test_unavailable_model_uses_durable_cooldown_before_auto_recovery(tmp_path):
    from datetime import datetime, timezone
    _, owner, job = jobs(tmp_path)
    run = SimpleNamespace(run_id="r", agent_plan=[], status="paused_model_unavailable",
                          classification="PROVIDER_UNAVAILABLE", retry_after_ms=None)
    first = owner.update_run(job["job_id"], run)
    first_due = datetime.fromisoformat(first["retry_due_at"].replace("Z", "+00:00"))
    assert 28 < (first_due - datetime.now(timezone.utc)).total_seconds() <= 37.5
    assert owner.update_run(job["job_id"], run)["retry_due_at"] == first["retry_due_at"]
    owner.mutate(job["job_id"], lambda value: value.update(auto_recovery_count=1, retry_due_at=None))
    second = owner.update_run(job["job_id"], run)
    second_due = datetime.fromisoformat(second["retry_due_at"].replace("Z", "+00:00"))
    assert 58 < (second_due - datetime.now(timezone.utc)).total_seconds() <= 75


def test_unknown_checkpoint_version_cannot_be_resumed(tmp_path):
    store,_,_=jobs(tmp_path)
    event=store.append_event("s",{"event_type":"turn.settled","run_id":"r"})
    store.save_run_checkpoint("r","s",turn=1,session_seq=event["sequence"],payload={})
    with closing(sqlite3.connect(store.path)) as conn:
        conn.execute("UPDATE agent_run_checkpoints SET payload_json=replace(payload_json,'forgex.agent-checkpoint.v1','forgex.agent-checkpoint.v999')")
        conn.commit()
    with pytest.raises(ValueError,match="VERSION_UNSUPPORTED"):
        store.latest_run_checkpoint("r")


def test_quality_fixture_preserves_five_historical_requests():
    import hashlib
    import json
    fixture_root=Path(__file__).resolve().parents[1]/"fixtures"
    original_bytes=(fixture_root/"beta-agent-prompts.json").read_bytes()
    original=json.loads(original_bytes)
    suite=json.loads((fixture_root/"agent-quality-suite.json").read_bytes())
    assert len(suite["cases"])==30
    assert suite["historical_fixture_sha256"]==hashlib.sha256(original_bytes).hexdigest()
    assert len({item["id"] for item in suite["cases"]})==30
    for historical in original["cases"]:
        case=next(item for item in suite["cases"] if item["id"]==historical["id"])
        for key in ("request","follow_up","clarification_answer"):
            assert case[key]==historical[key]


def test_untrusted_build_and_verify_child_never_launch_project_code(tmp_path):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.canonical_runtime import ToolInvocation
    from tests.unit.test_agent_verification_freshness import Builder, progress
    from tests.unit.test_tool_runtime import SequencePlanner
    async def scenario():
        builder=Builder()
        loop=AutonomousForgeAgentLoop(workspace_root=tmp_path,planner=SequencePlanner([]),platformio=builder,
            progress=progress,policy=BUILD_REPAIR_PROFILE.policy(),trusted_project=False)
        result=await loop._build("esp32dev")
        assert result["output"]["error_code"]=="AGENT_PROJECT_TRUST_REQUIRED"
        assert builder.calls==0
    asyncio.run(scenario())


def test_accepted_choice_memory_has_user_provenance_and_invalidates_revised_choice(tmp_path):
    from backend.agent_runtime.memory import ProjectMemoryService
    store,_,_=jobs(tmp_path)
    memory=ProjectMemoryService(store)
    for value in ("cloud: MQTT","cloud: MQTT","cloud: ThingSpeak"):
        memory.remember_choices(project_id="p",session_id="s",contract={"revision":"revision","accepted_choices":[value,"secret: password"]})
    entries=memory.active("p")
    assert len(entries)==1
    assert entries[0]["value"]=="cloud: ThingSpeak"
    assert entries[0]["source_type"]=="user_clarification"
    assert entries[0]["workspace_revision"] is None


def test_complex_review_requires_read_evidence_and_all_requirement_ids(tmp_path,monkeypatch):
    from backend.agent_runtime import autonomous_loop
    from backend.agent_runtime.api_planner_provider import ApiPlannerError
    from backend.agent_runtime.task_contract import verify_requirement_evidence
    from tests.unit.test_agent_verification_freshness import progress
    from tests.unit.test_requirement_review_response import planner_for
    workspace=tmp_path/"workspace"
    workspace.mkdir()
    contract=create_task_contract("\n".join("Requirement "+str(i) for i in range(8)),project_id="p",session_id="s",workspace=workspace,intent="generate")
    (workspace/"main.cpp").write_text("int led = 2;\n")
    report={"checks":[{"id":item["id"],"verdict":"satisfied","reason":"Current source meets the requirement."} for item in contract["requirements"]]}
    incomplete={"checks":report["checks"][:-1]}
    planner, adapter = planner_for(tmp_path,monkeypatch,[report,incomplete,incomplete])
    async def scenario():
        loop=autonomous_loop.AutonomousForgeAgentLoop(workspace_root=workspace,planner=planner,platformio=object(),progress=progress,task_contract=contract)
        revision=autonomous_loop._snapshot_for_context(workspace).revision
        loop.requirement_report=verify_requirement_evidence(contract,[{"id":item["id"],"status":"implemented",
            "evidence":[{"path":"main.cpp","quote":"int led = 2;"}]} for item in contract["requirements"]],workspace,revision)
        assert (await loop._review_completion(revision))["passed"]
        assert "int led = 2;" in adapter.requests[0].prompt
        assert loop.tool_count==0  # Host reads complete files without model tool exploration.
        assert loop.loop_guard.remaining_tool_calls==100
        with pytest.raises(ApiPlannerError) as exc:
            await loop._review_completion(revision)
        assert exc.value.safe_code == "SEMANTIC_REVIEW_INCOMPLETE"
    asyncio.run(scenario())


@pytest.mark.parametrize("cycle",range(20))
@pytest.mark.parametrize("fault",["settled","user_pause","untrusted","workspace_conflict","unknown_effect"])
def test_reopen_fault_matrix(tmp_path,cycle,fault):
    """100 deterministic owner restarts: 20 repetitions at five recovery boundaries."""
    async def scenario():
        workspace=tmp_path/"project"
        workspace.mkdir()
        (workspace/"main.cpp").write_text("original")
        changes=ChangeSetService(state_root=tmp_path/"changes",staging_root=tmp_path/"staging")
        database=tmp_path/"agent.sqlite3"
        attempts=[]
        async def executor(request):
            attempts.append(request.run_id)
            if len(attempts)==1:
                Path(request.active_workspace_root,"main.cpp").write_text("saved edit")
                await request.progress_callback("turn.settled",{"turn":2,"_resume_checkpoint":{
                    "turn":2,"tool_calls_used":2,"elapsed_seconds":4,"observations":[],
                    "authorized_paths":["main.cpp"],"deleted_paths":[],"plan":[],
                    "provider_budget":{"requests":3,"output_tokens":4096,"input_bytes":100}}})
                return {"success":False,"classification":"BUILD_ERROR","message":"Stopped after a settled turn"}
            assert Path(request.active_workspace_root,"main.cpp").read_text()=="saved edit"
            return {"success":True,"classification":"AGENT_V3_COMPLETED","build_verified":True,"build_result":{"success":True}}
        def owner():
            return AgentOrchestrator(change_service=changes,provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(database),run_factory=ProductAgentRun,autonomous_executor=executor)
        first=owner()
        run=await first.start_run(project_id="p",active_workspace_root=workspace,instruction="repair firmware",
            provider_id="verified_template",autonomy="build_only",session_id="s")
        await first._tasks[run.run_id]
        run.classification="AGENT_RUNTIME_INTERRUPTED"
        first._persist_run(run)
        job=first.jobs.get(run.job_id)
        if fault=="user_pause":
            job.update(paused_by="user",status="paused")
            first.jobs.save(job)
        if fault!="untrusted":
            first.jobs.trust("p",str(workspace.resolve()),True)
        if fault=="workspace_conflict":
            (workspace/"main.cpp").write_text("external change")
        if fault=="unknown_effect":
            first.store.operation("effect",""+run.run_id,"hardware.flash",status="started",payload={"port":"COM7"})
            first.store.interrupt_operations(run.run_id)
        await first.close()
        reopened=owner()
        reopened.auto_recovery_enabled=True
        try:
            await reopened.recover_jobs()
            if fault=="settled":
                replacement=reopened.get_run(run.run_id).recovered_by_run_id
                assert replacement
                await reopened._tasks[replacement]
                assert len(attempts)==2
                await reopened.recover_jobs()
                assert len(attempts)==2
                assert reopened.get_run(replacement).job_id==run.job_id
            else:
                assert len(attempts)==1
                assert not reopened.get_run(run.run_id).recovered_by_run_id
            assert (workspace/"main.cpp").read_text()==("external change" if fault=="workspace_conflict" else "original")
            assert reopened.jobs.get(run.job_id)["consumed"]["requests"]>=3
        finally:
            await reopened.close()
    asyncio.run(scenario())
