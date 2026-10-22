import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend.agent_runtime.engine import AsyncAgentEngine, PhaseBudget
from backend.agent_runtime.intake import AgentIntakeStore
from backend.agent_runtime.jobs import AgentJobStore
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.admission import ProviderAdmissionController
from backend.model_router.circuit import ProviderCircuitBreaker
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.providers.openai_provider import OpenAIProvider
from backend.model_router.router import ModelRouterService
from backend.model_router import request_recovery
from backend.services.llm_service import LLMProvider, LLMProviderError, OpenAIService
from tests.unit.test_router_product_planner import Adapter, PLAN, registry


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("hint", [None, "120", "Sat, 03 Oct 2037 00:00:00 GMT"])
def test_physical_failures_open_circuit_and_preserve_original_http_error(tmp_path, monkeypatch, stream, hint):
    models = registry(tmp_path)
    sent = []
    reserved = []
    monkeypatch.setattr(request_recovery, "transient_retry_delay_ms", lambda *a, **k: 0)
    def respond(request):
        sent.append(request)
        assert b"recovery_hook" not in request.content
        return httpx.Response(503, headers={"retry-after": hint} if hint else {}, json={"error": {"message": "Maintenance"}})
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter = OpenAIProvider(models)
            adapter._service = lambda model_id=None: OpenAIService(api_key="test", model=model_id, client=client)
            router = ModelRouterService(registry=models, providers={"openai": adapter})
            context = RequestExecutionContext(budget={"_persist": lambda value: reserved.append(dict(value))})
            request = ModelRequest(prompt="hello", provider_id="openai", model_id="gpt-4.1-mini", execution_context=context)
            with pytest.raises(LLMProviderError) as caught:
                if stream:
                    async for _ in router.stream_model(request):
                        pass
                else:
                    await router.generate_model(request)
            assert caught.value.status_code == 503
            assert caught.value.message == "Maintenance"
            assert caught.value.details["circuit_open"]
            assert context.attempts["used"] == len(sent) == len(reserved) == (1 if hint else 2)
            # A new logical request cannot dispatch into the persisted cooldown.
            with pytest.raises(LLMProviderError) as gate:
                await router.generate_model(request)
            assert gate.value.details["request_reached_provider"] is False
    asyncio.run(execute())


def test_retry_wait_releases_distributed_endpoint_capacity(tmp_path, monkeypatch):
    models = registry(tmp_path)
    async def execute():
        first = ModelRouterService(registry=models)
        second = ProviderAdmissionController(path=first.admission.path, limits={"openai": 1}, queue_timeout_seconds=.05)
        first.admission._limits["openai"] = 1
        request = ModelRequest(prompt="hello", provider_id="openai", model_id="gpt-4.1-mini", execution_context=RequestExecutionContext())
        async def hook(event):
            if event["event_type"] == "model.retry.scheduled":
                async with second.slot("openai", "gpt-4.1-mini", endpoint_identity=first._circuit_identity("openai", "gpt-4.1-mini")):
                    pass
        request.execution_context.recovery_hook = hook
        monkeypatch.setattr(request_recovery, "transient_retry_delay_ms", lambda *a, **k: 0)
        failure = None
        try:
            async with first._physical_slot(request):
                raise LLMProviderError("Temporary", provider=LLMProvider.OPENAI, status_code=503, retryable=True)
        except LLMProviderError as exc:
            failure = exc
        assert await request_recovery.retry_wait(request, failure, 1)
    asyncio.run(execute())


def test_distributed_capacity_and_cancelled_waiters(tmp_path):
    first = ProviderAdmissionController(path=tmp_path / "slots.sqlite3", queue_timeout_seconds=.02)
    second = ProviderAdmissionController(path=first.path, queue_timeout_seconds=.02)
    async def execute():
        async with first.slot("nvidia_nim", "model", endpoint_identity="same"):
            with pytest.raises(LLMProviderError) as caught:
                async with second.slot("nvidia_nim", "model", endpoint_identity="same"):
                    pytest.fail("capacity was not shared")
            assert caught.value.code == "PROVIDER_BUSY"
            waiter = asyncio.create_task(second.slot("nvidia_nim", "model", endpoint_identity="same").__aenter__())
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with second.slot("nvidia_nim", "model", endpoint_identity="same"):
            pass
    asyncio.run(execute())


def test_stale_probe_cannot_close_a_newer_circuit(tmp_path):
    now = [0.0]
    circuit = ProviderCircuitBreaker(tmp_path / "circuit.sqlite3", clock=lambda: now[0], jitter=lambda a,b:a)
    failure = LLMProviderError("outage", provider=LLMProvider.OPENAI, status_code=503, retryable=True)
    circuit._settle("endpoint", None, failure)
    circuit._settle("endpoint", None, failure)
    now[0] = 31
    old = circuit._admit("endpoint")
    now[0] = 382
    new = circuit._admit("endpoint")
    circuit._settle("endpoint", new, failure)
    circuit._settle("endpoint", old)
    assert circuit.health("endpoint")["state"] == "open"


def test_endpoint_capacity_is_shared_with_another_process(tmp_path):
    import subprocess
    import sys
    script = """
import asyncio, sys
from pathlib import Path
from backend.model_router.admission import ProviderAdmissionController
async def main():
    owner = ProviderAdmissionController(path=Path(sys.argv[1]))
    async with owner.slot('nvidia_nim', 'model', endpoint_identity='same'):
        print('acquired', flush=True)
        await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())
"""
    path = tmp_path / "process-slots.sqlite3"
    child = subprocess.Popen([sys.executable, "-c", script, str(path)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "acquired"
        owner = ProviderAdmissionController(path=path, queue_timeout_seconds=.02)
        async def busy():
            with pytest.raises(LLMProviderError) as caught:
                async with owner.slot("nvidia_nim", "model", endpoint_identity="same"):
                    pytest.fail("another process bypassed admission")
            assert caught.value.code == "PROVIDER_BUSY"
        asyncio.run(busy())
        _, stderr = child.communicate("release\n", timeout=15)
        assert child.returncode == 0, stderr
        async def released():
            async with owner.slot("nvidia_nim", "model", endpoint_identity="same"):
                pass
        asyncio.run(released())
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=15)


def test_reviewer_can_use_reserved_finish_time(tmp_path):
    models = registry(tmp_path)
    adapter = Adapter([PLAN])
    planner = RouterProductPlanner(ModelRouterService(registry=models, providers={"openai": adapter}), provider_id="openai", model_id="gpt-4.1-mini")
    planner.bind_deadline(__import__("time").monotonic() + 90)
    planner.bind_verification_reserve(180)
    reviewer = planner.for_role("review")
    reviewer.request_turn(task="Review", tool_results=(), allowed_tools=("read_file",), run_id="review", turn=1)
    assert len(adapter.requests) == 1
    assert reviewer._verification_reserve_s == 0
    assert reviewer._run_budget is planner._run_budget


def test_phase_allocations_use_recent_build_duration():
    assert PhaseBudget.for_task(True).generation_reserve == 180
    assert PhaseBudget.for_task(True, [150, 200]).build_seconds == 200
    assert PhaseBudget.for_task(True, [500]).build_seconds == 300
    assert PhaseBudget.for_task(False).build_seconds == 0
    assert PhaseBudget.for_task(True).build_deadline(200, now=0) == 120
    assert PhaseBudget.for_task(True, [200]).build_deadline(200, now=0) == 140


def stores(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "jobs.sqlite3")
    jobs = AgentJobStore(store)
    job = jobs.create(project_id="p", session_id="s", workspace_root=tmp_path, run_id="r")
    # Scheduler policy tests explicitly opt in, independent of the rollout flag.
    job = jobs.mutate(job["job_id"], lambda value: value.update(recovery_policy="safe_auto"))
    return store, jobs, job


def outage(jobs, job):
    run = SimpleNamespace(run_id="r", status="paused_model_unavailable", classification="PROVIDER_UNAVAILABLE", provider_id="nvidia_nim", model_id="model", retry_after_ms=0, http_status=503, request_reached_provider=True)
    return jobs.update_run(job["job_id"], run)


def test_outage_deadline_persists_and_competing_schedulers_claim_once(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEX_ENABLE_SHARED_AGENT_ENGINE", "1")
    store, jobs, job = stores(tmp_path)
    saved = outage(jobs, job)
    original_deadline = saved["outage"]["deadline_at"]
    jobs.mutate(job["job_id"], lambda v: v.update(retry_due_at="2020-01-01T00:00:00Z"))
    restarted = AgentJobStore(AgentOrchestrationStore(store.path))
    assert restarted.get(job["job_id"])["outage"]["deadline_at"] == original_deadline
    assert jobs.claim_recovery(job["job_id"], "one")
    assert not restarted.claim_recovery(job["job_id"], "two")
    assert jobs.get(job["job_id"])["outage"]["cycles"] == 1


@pytest.mark.parametrize("block", ["deadline", "cycles", "long_hint"])
def test_outage_limits_pause_without_resetting_task_budgets(tmp_path, block):
    _, jobs, job = stores(tmp_path)
    saved = outage(jobs, job)
    saved["consumed"]["requests"] = 7
    saved["retry_due_at"] = "2020-01-01T00:00:00Z"
    if block == "deadline":
        saved["outage"]["deadline_at"] = "2020-01-01T00:00:00Z"
    elif block == "cycles":
        saved["outage"]["cycles"] = 3
    else:
        saved["retry_due_at"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    jobs.save(saved)
    assert not jobs.claim_recovery(job["job_id"], "one")
    result = jobs.get(job["job_id"])
    assert result["blocking_reason"] in {"AGENT_AUTO_RECOVERY_LIMIT", "AGENT_OUTAGE_WINDOW_EXHAUSTED"}
    jobs.begin_manual_recovery(job["job_id"])
    assert jobs.get(job["job_id"])["consumed"]["requests"] == 7


def test_durable_wait_does_not_consume_active_runtime(tmp_path):
    _, jobs, job = stores(tmp_path)
    saved = outage(jobs, job)
    saved["consumed"]["elapsed_seconds"] = 42
    saved["durable_wait_started_at"] = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    jobs.save(saved)
    assert jobs.public(jobs.get(job["job_id"]))["durable_wait_seconds"] >= 30
    assert jobs.get(job["job_id"])["consumed"]["elapsed_seconds"] == 42
    jobs.attach(job["job_id"], "replacement")
    result = jobs.public(jobs.get(job["job_id"]))
    assert result["durable_wait_seconds"] >= 30
    assert result["budget"]["consumed"]["elapsed_seconds"] == 42


def test_intake_accounting_survives_restart_and_attaches_once(tmp_path):
    store, jobs, job = stores(tmp_path)
    intakes = AgentIntakeStore(store)
    intake = intakes.create("s", "p", "request")
    intakes.reserve(intake["intake_id"], {"requests": 2, "output_tokens": 1000, "input_bytes": 50})
    intakes.finish(intake["intake_id"], "paused_model_unavailable", 10)
    restored = AgentIntakeStore(AgentOrchestrationStore(store.path)).create("s", "p", "request")
    assert restored["intake_id"] == intake["intake_id"]
    for _ in range(2):
        intakes.attach(intake["intake_id"], job["job_id"])
    result = jobs.get(job["job_id"])["consumed"]
    assert result["requests"] == 2
    assert result["elapsed_seconds"] == 10


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_shared_engine_supports_router_and_compatibility_planners(monkeypatch, enabled):
    monkeypatch.setenv("FORGEX_ENABLE_SHARED_AGENT_ENGINE", enabled)
    class Planner:
        def request_turn(self, **kwargs):
            return "compat"
        async def request_turn_async(self, **kwargs):
            return "async"
    assert asyncio.run(AsyncAgentEngine().request(Planner(), task="task")) == ("async" if enabled == "1" else "compat")


@pytest.mark.parametrize("enabled", ["0", "1"])
def test_shared_engine_summary_callbacks_use_owning_loop(monkeypatch, enabled):
    monkeypatch.setenv("FORGEX_ENABLE_SHARED_AGENT_ENGINE", enabled)
    class Planner:
        async def request_turn_async(self, *, on_summary, **kwargs):
            await on_summary("summary")
            return "done"
        def request_turn_stream(self, *, on_summary, **kwargs):
            on_summary("summary")
            return "done"
    async def execute():
        loop = asyncio.get_running_loop()
        received = []
        async def summary(value):
            assert asyncio.get_running_loop() is loop
            received.append(value)
        assert await AsyncAgentEngine().request(Planner(), on_summary=summary) == "done"
        assert received == ["summary"]
    asyncio.run(execute())


def test_rollout_disabled_migrates_jobs_to_manual_without_resetting_budget(tmp_path, monkeypatch):
    store, jobs, job = stores(tmp_path)
    saved = outage(jobs, job)
    saved["schema_version"] = 1
    saved["consumed"]["requests"] = 7
    deadline = saved["outage"]["deadline_at"]
    # Seed an older payload; using save would already perform the upgrade.
    from backend.agent_runtime.orchestration_store import _encode
    with store._transaction() as db:
        db.execute("UPDATE agent_jobs SET payload_json=? WHERE job_id=?", (_encode(saved), job["job_id"]))
    monkeypatch.setenv("FORGEX_ENABLE_SHARED_AGENT_ENGINE", "0")
    restarted = AgentJobStore(AgentOrchestrationStore(store.path))
    restored = restarted.get(job["job_id"])
    assert restored["schema_version"] == 3
    assert restored["recovery_policy"] == "manual"
    assert restored["consumed"]["requests"] == 7
    assert restored["outage"]["deadline_at"] == deadline
    fresh = restarted.create(project_id="p", session_id="new", workspace_root=tmp_path, run_id="new")
    assert fresh["recovery_policy"] == "manual"
