"""Incident regressions exercised through real adapter and durable-store seams."""
import asyncio
import json
import time
from types import SimpleNamespace
import pytest

from backend.contracts.diagnostics import apply_provider_diagnostics, safe_provider_diagnostics
from backend.agent_runtime.api_planner_provider import ApiPlannerError, ApiPlannerClassification
from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.agent_runtime.verification import bounded_review_units, review_requirements
from backend.agent_runtime.deliverables import infer_deliverables, project_deliverables
from backend.services.build_operations import BuildOperations
from tests.unit.test_tool_runtime import make_runtime, SequencePlanner, plan


DIAGNOSTICS = dict(physical_attempt_id="incident-attempt", response_headers_received=True,
    request_reached_provider=True, http_status=200, first_event_ms=2311, last_event_ms=2390,
    stream_event_count=6, completion_received=False, timeout_stage="stream_idle", duration_ms=62390,
    last_failure_origin="provider_transport", last_failure_code="PROVIDER_TIMEOUT")


def test_real_tool_adapter_keeps_incident_diagnostics_across_storage(tmp_path):
    runtime, _, _ = make_runtime(tmp_path)
    class Interrupted(SequencePlanner):
        def request_turn(self, **kwargs):
            if self.seen_results:
                raise ApiPlannerError(ApiPlannerClassification.API_NETWORK_ERROR, "PROVIDER_TIMEOUT")
            return super().request_turn(**kwargs)
        def to_safe_metadata(self):
            return {**DIAGNOSTICS, "api_key": "private", "prompt": "private"}
    from backend.agent_runtime.tool_policy import product_agent_policy
    result = runtime.run_product(task="write firmware", planner=Interrupted([plan()]), policy=product_agent_policy())
    assert result.provider_diagnostics == DIAGNOSTICS
    run = ProductAgentRun("r", "p", "nvidia_nim", status="failed", classification="PROVIDER_TIMEOUT")
    apply_provider_diagnostics(run, result.provider_diagnostics)
    store = AgentOrchestrationStore(tmp_path / "incident.sqlite3")
    store.save_run("r", "s", "p", "failed", run.to_safe_dict())
    payload = AgentOrchestrationStore(store.path).load_run("r")
    assert all(payload["provider_diagnostics"][key] == value for key, value in DIAGNOSTICS.items())
    assert payload["failure"]["physical_attempt_id"] == "incident-attempt"
    assert "private" not in json.dumps(payload)


def test_diagnostics_discard_invalid_fields_without_dropping_valid_evidence():
    assert safe_provider_diagnostics({"stream_event_count": -1, "http_status": 200, "completion_received": "yes", "raw_body": "secret"}) == {"http_status": 200}


def test_actual_http_stream_survives_planner_and_workspace_adapter(tmp_path, monkeypatch):
    import httpx
    from backend.agent_runtime.router_planner import RouterProductPlanner
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.model_router.router import ModelRouterService
    from backend.model_router.providers.openai_provider import OpenAIProvider
    from backend.services.llm_service import OpenAIService
    from backend.contracts.transport import TransportPolicy
    from tests.unit.test_router_product_planner import registry
    from tests.unit.test_provider_transport_policy import ScriptedStream, packet
    monkeypatch.setenv("FORGEX_ENABLE_REQUEST_RECOVERY", "0")
    requests = []
    streams = []
    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            chunks = [(0, packet({"choices":[{"delta":{"tool_calls":[{"index":0,"id":"write-1","type":"function","function":{"name":"write_file","arguments":json.dumps({"path":"safe.txt","content":"saved\n"})}}]},"finish_reason":"tool_calls"}], "usage":{"completion_tokens":15,"prompt_tokens":20}})), (0, b"data: [DONE]\n\n")]
        else:
            chunks = [(0, packet({"choices":[{"delta":{"content":"partial private response"}}]}))]
        stream = ScriptedStream(chunks)
        streams.append(stream)
        return httpx.Response(200, headers={"content-type":"text/event-stream"}, stream=stream)
    class Provider(OpenAIProvider):
        def _service(self, model_id=None):
            return OpenAIService(api_key="private", model=model_id or "gpt-4.1-mini", client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        def _configure_service(self, service, request):
            request.execution_context.transport_policy = TransportPolicy(first_event_seconds=.1,idle_seconds=.02,total_seconds=.2)
            super()._configure_service(service, request)
    models = registry(tmp_path)
    router = ModelRouterService(registry=models, providers={"openai":Provider(models)})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")
    runtime, active, changes = make_runtime(tmp_path)
    result = runtime.run_product(task="write a safe file", planner=planner, policy=product_agent_policy())
    assert len(requests) == 2 and all(stream.closed for stream in streams)
    diagnostic = result.provider_diagnostics
    assert diagnostic["http_status"] == 200 and diagnostic["response_headers_received"]
    assert diagnostic["timeout_stage"] == "stream_idle" and diagnostic["stream_event_count"] == 1
    assert diagnostic["physical_attempt_id"] and not diagnostic["completion_received"]
    assert diagnostic["first_event_ms"] is not None and diagnostic["last_event_ms"] is not None
    assert "private response" not in json.dumps(diagnostic)
    assert not (active / "safe.txt").exists() and result.change_set_id
    assert changes.get(result.change_set_id).status == "draft"


def test_attempt_settlement_is_idempotent_durable_and_never_refunds_unknown_output(tmp_path):
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "budget.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    charge = {"requests": 1, "input_bytes": 100, "output_tokens": 2048, "_attempt_id": "completed"}
    owner.reserve_provider_attempt(job["job_id"], "r", charge)
    old = owner.get(job["job_id"])
    owner.reserve_provider_attempt(job["job_id"], "r", charge)
    owner.settle_provider_attempt(job["job_id"], "completed", 42)
    owner.settle_provider_attempt(job["job_id"], "completed", 42)
    owner.save(old)  # A stale projection cannot restore an old reservation.
    owner.reserve_provider_attempt(job["job_id"], "r", {**charge, "_attempt_id": "interrupted"})
    reopened = AgentJobStore(AgentOrchestrationStore(owner.store.path))
    current = reopened.get(job["job_id"])
    assert current["consumed"]["requests"] == 2
    assert current["consumed"]["output_tokens"] == 2090
    assert reopened.public(current)["budget"]["accounting"] == {"reserved_output_tokens": 4096,
        "actual_completed_output_tokens": 42, "unresolved_output_tokens": 2048, "physical_attempts": 2}
    with pytest.raises(ValueError, match="CONFLICT"):
        reopened.settle_provider_attempt(job["job_id"], "completed", 40)


def test_large_review_covers_exact_spans_and_aggregates_same_revision():
    text = "int value = 1;\n" * 11_000
    contract = {"revision": 1, "requirements": [{"id": "req-1", "text": "initialize value"}], "exclusions": []}
    report = {"references_valid": True, "workspace_revision": "rev", "task_revision": 1,
              "checks": [{"id": "req-1", "evidence": [{"path": "src/main.cpp", "start_line": 1, "end_line": 1}]}]}
    units, index = bounded_review_units(contract, report, lambda _: text)
    assert len(units) == 3
    assert "".join(item["source"] for item in units) == text
    assert units[-1]["end_line"] == index["src/main.cpp"]["lines"] == 11000
    class Reviewer:
        async def request_turn_async(self, **kwargs):
            return SimpleNamespace(final=True, tool_calls=(), summary=json.dumps({"checks": [{"id": "req-1", "verdict": "satisfied", "reason": "initialized in reviewed source"}]}))
    result = asyncio.run(review_requirements(Reviewer(), contract, report, lambda _: text, "rev", deadline=time.monotonic()+5, run_id="r"))
    assert result["valid"] and result["semantic_review"]["coverage_complete"]
    assert len(result["semantic_review"]["units"]) == 3
    assert not result["semantic_review"]["execution_verified"]


def test_unrelated_markdown_never_satisfies_requested_documentation():
    contract = {"requirements": [{"id": "r1", "text": "Write wiring and calibration documentation"}]}
    contract["deliverables"] = infer_deliverables(contract)
    item = project_deliverables(contract, paths=["old-notes.md"], build={}, report={}, source_revision="rev")[0]
    assert item["status"] == "missing" and item["topics"] == ["wiring", "calibration"]


def test_build_operation_double_accept_and_restart_do_not_replay(tmp_path):
    async def scenario():
        store = AgentOrchestrationStore(tmp_path / "build.sqlite3")
        owner = BuildOperations(store)
        calls = []
        async def execute():
            calls.append(1)
            return {"result": {"success": True}}, "rev"
        args = dict(project_id="p", environment="esp32", source_revision="rev", execute=execute)
        owner.accept("operation-1", **args)
        owner.accept("operation-1", **args)
        await asyncio.gather(*owner.tasks.values())
        assert owner.get("operation-1")["state"] == "passed" and len(calls) == 1
        reopened = BuildOperations(store)
        reopened.accept("operation-1", **args)
        assert not reopened.tasks and len(calls) == 1
        async def never():
            await asyncio.Event().wait()
        owner.accept("operation-2", **{**args, "execute": never})
        await asyncio.sleep(0)
        await owner.close()
        assert BuildOperations(store).get("operation-2")["state"] == "interrupted"
    asyncio.run(scenario())


def test_generation_leaves_verification_budget_but_review_can_use_it():
    from backend.model_router.models import ModelRequest
    from backend.model_router.run_budget import charge_attempt, new_budget, AgentBudgetExceeded
    budget = new_budget({"requests": 118, "output_tokens": 240000})
    budget["_phase_reserve"] = {"requests": 2, "output_tokens": 2048}
    request = ModelRequest(prompt="firmware", max_tokens=1024,
        metadata={"forgex_run_budget":budget, "agent_phase":"generation"})
    with pytest.raises(AgentBudgetExceeded) as exc:
        charge_attempt(request)
    assert exc.value.code == "AGENT_VERIFICATION_RESERVE_REACHED" and budget["requests"] == 118
    from dataclasses import replace
    charge_attempt(replace(request, metadata={**request.metadata, "agent_phase":"review"}))
    assert budget["requests"] == 119
    budget["requests"] = 120
    with pytest.raises(AgentBudgetExceeded) as exc:
        charge_attempt(request)
    assert exc.value.code == "AGENT_PROVIDER_BUDGET_EXCEEDED"


def test_review_resumes_sealed_units_and_source_change_invalidates_them(tmp_path):
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "review.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    cache = {"load":lambda key:owner.review_units(job["job_id"], key),
             "save":lambda key, unit:owner.save_review_unit(job["job_id"], key, unit)}
    text = "int value = 1;\n" * 11000
    contract = {"revision":1, "requirements":[{"id":"req-1","text":"initialize value"}], "exclusions":[]}
    report = {"references_valid":True, "workspace_revision":"rev", "task_revision":1,
        "checks":[{"id":"req-1", "evidence":[{"path":"main.cpp","start_line":1,"end_line":1}]}]}
    class Reviewer:
        _review_cache = cache
        def __init__(self, fail=False): self.calls = 0; self.fail = fail
        async def request_turn_async(self, **kwargs):
            self.calls += 1
            if self.fail and self.calls == 2: raise TimeoutError("interrupted")
            return SimpleNamespace(final=True, tool_calls=(), summary=json.dumps({"checks":[{"id":"req-1", "verdict":"satisfied", "reason":"reviewed span"}]}))
    def review(reviewer, source=text):
        return asyncio.run(review_requirements(reviewer, contract, report, lambda _:source, "rev", deadline=time.monotonic()+5, run_id="r"))
    with pytest.raises(TimeoutError): review(Reviewer(True))
    resumed = Reviewer()
    assert review(resumed)["valid"] and resumed.calls == 3  # two remaining units + aggregate
    changed = Reviewer()
    assert review(changed, text.replace("= 1", "= 2"))["valid"] and changed.calls == 4
    with owner.store._transaction() as connection:
        connection.execute("UPDATE agent_review_units SET checksum='invalid'")
    corrupted = Reviewer()
    assert review(corrupted)["valid"] and corrupted.calls == 4


def test_missing_local_dependency_cannot_be_certified_by_small_review():
    contract = {"requirements":[{"id":"r1","text":"initialize sensor"}], "exclusions":[]}
    report = {"references_valid":True, "workspace_revision":"rev",
        "checks":[{"id":"r1","evidence":[{"path":"main.cpp","start_line":1,"end_line":1}]}]}
    def read(path):
        if path == "main.cpp": return '#include "missing.h"\nvoid setup() {}\n'
        raise FileNotFoundError(path)
    class Reviewer:
        async def request_turn_async(self, **kwargs):
            return SimpleNamespace(final=True, tool_calls=(), summary=json.dumps({"checks":[{"id":"r1", "verdict":"satisfied", "reason":"looks initialized"}]}))
    result = asyncio.run(review_requirements(Reviewer(), contract, report, read, "rev", deadline=time.monotonic()+5, run_id="r"))
    assert not result["valid"] and not result["semantic_review"]["dependency_coverage_complete"]


def test_incremental_projection_reuses_checkpoint_and_mutations_check_integrity(tmp_path, monkeypatch):
    import threading
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.agent_runtime.projection import SessionProjectionEngine
    store = AgentOrchestrationStore(tmp_path / "projection.sqlite3")
    for number in range(20):
        store.append_event("s", {"run_id":"r", "event_type":"model.attempt.settled", "content":str(number)})
    engine = SessionProjectionEngine()
    owner = SimpleNamespace(store=store, projections=engine, _lock=threading.RLock(), _session_for_run={}, _transient_events={})
    calls = []
    apply = engine.apply_event
    monkeypatch.setattr(engine, "apply_event", lambda state, event:(calls.append(event["sequence"]), apply(state,event))[1])
    assert AgentOrchestrator.session_projection(owner,"s")["session_seq"] == 20
    calls.clear()
    assert AgentOrchestrator.session_projection(owner,"s")["assistant_content"] == "19" and not calls
    # Empty WAL recreation on read must not invalidate a warmed integrity cache.
    AgentOrchestrator.session_projection(owner,"s")
    verification_calls = []
    verify = store._verify_snapshot
    monkeypatch.setattr(store, "_verify_snapshot", lambda connection, session:(verification_calls.append(session), verify(connection,session))[1])
    AgentOrchestrator.session_projection(owner,"s")
    assert not verification_calls
    store.append_event("s", {"run_id":"r", "event_type":"model.attempt.settled", "content":"new"})
    assert AgentOrchestrator.session_projection(owner,"s")["assistant_content"] == "new" and calls == [21]
    with store._transaction() as connection:
        connection.execute("UPDATE agent_events SET payload_json='{}' WHERE session_id='s' AND sequence=1")
    with pytest.raises(ValueError, match="QUARANTINED"):
        store.acquire_lease("s", "owner")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("finish_reason", ["stop", "length"])
def test_ollama_known_completed_usage_settles_durable_reservation(tmp_path, monkeypatch, stream, finish_reason):
    import httpx
    from backend.model_router.models import ModelRequest, RequestExecutionContext
    from backend.model_router.providers.ollama import OllamaProvider
    from backend.model_router.run_budget import new_budget
    from tests.unit.test_router_product_planner import registry
    owner = AgentJobStore(AgentOrchestrationStore(tmp_path / "ollama.sqlite3"))
    job = owner.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    budget = new_budget()
    budget["_reserve"] = lambda charge:owner.reserve_provider_attempt(job["job_id"], "r", charge)
    budget["_settle"] = lambda identity, actual:owner.settle_provider_attempt(job["job_id"], identity, actual)
    envelope = {"model":"llama3.2", "message":{"role":"assistant","content":"complete"}, "done":True,
        "done_reason":finish_reason, "eval_count":7, "prompt_eval_count":10}
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs:client_type(transport=httpx.MockTransport(
        lambda request:httpx.Response(200, text=json.dumps(envelope)+"\n") if stream else httpx.Response(200,json=envelope)), **kwargs))
    provider = OllamaProvider(registry(tmp_path))
    request = ModelRequest(prompt="hello", model_id="llama3.2", max_tokens=1024, execution_context=RequestExecutionContext(budget=budget))
    async def call():
        if stream:
            return [event async for event in provider.generate_event_stream(request)]
        return await provider.generate(request)
    asyncio.run(call())
    assert owner.get(job["job_id"])["consumed"]["output_tokens"] == (7 if finish_reason == "stop" else 1024)
    assert owner.attempt_accounting(job["job_id"])["actual_completed_output_tokens"] == (7 if finish_reason == "stop" else 0)
