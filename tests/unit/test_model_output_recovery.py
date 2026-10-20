"""Regressions for the live truncated-response incident, using the real worker."""
import asyncio
import json
import time
import threading
from pathlib import Path

import pytest

from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.run_models import ProductAgentRun
from backend.agent_runtime.tool_contracts import ToolCall, parse_tool_name
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.contracts.diagnostics import apply_provider_diagnostics, safe_provider_diagnostics
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router.router import ModelRouterService
from backend.model_router.usage import UsageTracker
from backend.observability.diagnostics import live_log_path
from backend.services.llm_service import LLMResponseError
from tests.unit.test_opencode_engine import registry, upstream

MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def reply(handler, delta, *, finish="stop", tokens=1, reasoning=None):
    usage = {"prompt_tokens": 3, "completion_tokens": tokens, "total_tokens": tokens + 3}
    if reasoning is not None:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("x-request-id", "fixture-request")
    handler.end_headers()
    event = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}
    handler.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
    handler.wfile.flush()


def planner_for(tmp_path, url, **kwargs):
    reg = registry(tmp_path)
    reg.configure_provider("openrouter", {"api_key": "fixture-secret", "base_url": url})
    router = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path / "usage.jsonl"))
    return RouterProductPlanner(router, provider_id="openrouter", model_id=MODEL, **kwargs)


def request(planner, *, observations=(), turn=1):
    return planner.request_turn(task="Create and verify a small ESP32 project.",
        tool_results=observations, allowed_tools=("write_file", "read_file"),
        run_id="output-recovery", turn=turn)


@pytest.mark.parametrize("needs_repair", [False, True])
def test_real_worker_recovers_encoded_requirement_checks_with_live_remaining_budget(tmp_path, needs_repair):
    checks = [{"id": "led", "status": "implemented", "evidence": [
        {"path": "src/main.cpp", "quote": "digitalWrite(LED_PIN, HIGH);"}]}]
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        value = "invalid JSON" if needs_repair and len(bodies) == 1 else json.dumps(checks)
        reply(handler, {"tool_calls": [{"index": 0, "id": f"verify-{len(bodies)}", "type": "function",
            "function": {"name": "verify_requirements", "arguments": json.dumps({"checks": value})}}]}, finish="tool_calls")
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        planner.bind_deadline(time.monotonic() + 470.766)
        planner.bind_verification_reserve(180)
        plan = planner.request_turn(task="Verify the LED requirement with exact source evidence.",
            tool_results=(), allowed_tools=("verify_requirements",), run_id="verify-checks", turn=1)
    assert plan.tool_calls[0].arguments["checks"] == checks
    assert len(bodies) == (2 if needs_repair else 1)
    diagnostics = planner.to_safe_metadata()
    assert diagnostics["toolplan_repair_attempted"] == needs_repair
    assert diagnostics["physical_request_count"] == len(bodies)
    assert diagnostics["repair_skip_reason"] is None
    if needs_repair:
        assert diagnostics["last_repair_outcome"] == "succeeded"


def test_useful_repair_fits_a_turn_shorter_than_the_maximum_repair_window(tmp_path):
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) == 1:
            reply(handler, {"reasoning_content": "thinking"}, finish="length")
        else:
            reply(handler, {"tool_calls": [{"index": 0, "id": "read", "type": "function",
                "function": {"name": "read", "arguments": json.dumps({"path": "src/main.cpp"})}}]}, finish="tool_calls")
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url, turn_timeout_s=90)
        assert request(planner).tool_calls[0].path == "src/main.cpp"
    assert len(bodies) == 2
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


def test_real_reasoning_only_truncation_repairs_then_preserves_tool_history(tmp_path, monkeypatch):
    log = tmp_path / "diagnostics.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log))
    log = live_log_path()
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) == 1:
            reply(handler, {"reasoning_content": "PRIVATE_REASONING_FIXTURE"},
                  finish="length", tokens=8192, reasoning=8192)
        elif len(bodies) == 2:
            reply(handler, {"tool_calls": [{"index": 0, "id": "write-complete", "type": "function",
                "function": {"name": "write", "arguments": json.dumps({"path": "src/main.cpp", "content": "void setup() {}\nvoid loop() {}\n"})}}]}, finish="tool_calls")
        else:
            reply(handler, {"content": "Source saved; compilation still needs verification."})
    project = tmp_path / "stage"
    project.mkdir()
    executor = ForgeXToolExecutor(write_root=project, policy=product_agent_policy())
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        plan = request(planner)
        diagnostics = safe_provider_diagnostics(planner.to_safe_metadata())
        assert diagnostics["initial_failure_code"] == "MODEL_OUTPUT_TRUNCATED"
        assert diagnostics["last_repair_outcome"] == "succeeded"
        first = diagnostics["initial_attempt_diagnostics"]
        assert first["finish_reason"] == "length"
        assert first["completion_received"] is True
        assert first["output_tokens"] == first["reasoning_tokens"] == 8192
        assert first["reasoning_event_count"] == 1
        assert first["text_event_count"] == first["tool_event_count"] == 0
        call = plan.tool_calls[0]
        output = executor.execute(ToolCall(parse_tool_name(call.tool_type), call.arguments))
        final = request(planner, observations=({"call_id": call.call_id, "tool": call.tool_type,
            "status": "completed", "output": output},), turn=2)
    assert final.final
    assert len(bodies) == 3
    assert bodies[0]["max_tokens"] == 8192
    assert bodies[1]["max_tokens"] == 16384
    assert bodies[2]["max_tokens"] == 16384  # Successful repair must not reset the next turn.
    saved_recovery = planner.output_recovery_state()
    resumed = RouterProductPlanner(planner.router, provider_id="openrouter", model_id=MODEL)
    resumed.restore_output_recovery(saved_recovery)
    assert resumed._response_allowance() == 16384
    assert saved_recovery["prefer_small_actions"] is True
    assert "reasoning" not in bodies[0]
    assert bodies[1]["reasoning"] == {"effort": "low"}
    assert "one complete next action" in bodies[1]["messages"][0]["content"]
    assert {body["model"] for body in bodies} == {MODEL}
    history = [c for m in bodies[2]["messages"] if m["role"] == "assistant" for c in m.get("tool_calls", [])]
    assert [c["id"] for c in history] == ["write-complete"]
    assert any(m.get("tool_call_id") == "write-complete" for m in bodies[2]["messages"])
    assert (project / "src/main.cpp").read_text() == "void setup() {}\nvoid loop() {}\n"
    assert "PRIVATE_REASONING_FIXTURE" not in log.read_text()
    assert "fixture-secret" not in log.read_text()
    failed = next(json.loads(line) for line in log.read_text().splitlines()
        if json.loads(line).get("event") == "provider.attempt.failed")
    assert failed["requested_output_tokens"] == failed["effective_output_tokens"] == 8192
    assert failed["reasoning_tokens"] == 8192


@pytest.mark.parametrize("partial", [False, True])
def test_truncated_content_or_tool_is_never_executed(tmp_path, partial):
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) == 1:
            delta = {"content": "I have compiled everything"}
            if partial:
                delta = {"tool_calls": [{"index": 0, "id": "partial", "type": "function",
                    "function": {"name": "write", "arguments": '{"path":"src/bad.cpp","content":"unfinished'}}]}
            reply(handler, delta, finish="length")
        else:
            reply(handler, {"content": "No file edits were made."})
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        plan = request(planner)
        assert plan.final and not plan.tool_calls
        assert plan.summary == "No file edits were made."
        assert len(bodies) == 2
        assert not planner._native_messages or not any(
            call.get("id") == "partial" for message in planner._native_messages
            for call in message.get("tool_calls", []))
    assert not (tmp_path / "src/bad.cpp").exists()


@pytest.mark.parametrize("budget", ["time", "output", "requests"])
def test_repair_is_not_dispatched_without_remaining_allowance(tmp_path, budget):
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        reply(handler, {"reasoning_content": "thinking"}, finish="length", tokens=body["max_tokens"])
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url, turn_timeout_s=20 if budget == "time" else 300)
        if budget != "time":
            planner.bind_job_budget({"consumed": {}, "limits": {"requests": 3 if budget == "requests" else 20,
                "output_tokens": 4000 if budget == "output" else 100000, "input_bytes": 1000000}}, lambda _: None)
        with pytest.raises(ApiPlannerError) as caught:
            request(planner)
    assert caught.value.safe_code == ("MODEL_OUTPUT_TRUNCATED" if budget == "time" else "AGENT_VERIFICATION_RESERVE_REACHED")
    assert len(bodies) == 1
    metadata = planner.to_safe_metadata()
    assert metadata["last_repair_outcome"] == "skipped"
    assert metadata["repair_skip_reason"] == {"time": "time_budget", "output": "output_budget", "requests": "request_budget"}[budget]
    assert metadata["toolplan_repair_attempted"] is False
    assert metadata["recovery_failure_code"] is None
    assert metadata["initial_failure_code"] == "MODEL_OUTPUT_TRUNCATED"


def test_repeated_invalid_streams_respect_shared_attempts_and_no_visible_text(tmp_path, monkeypatch):
    from backend.model_router import request_recovery
    real_sleep = asyncio.sleep
    async def fast_sleep(_):
        await real_sleep(0)
    monkeypatch.setattr(request_recovery.asyncio, "sleep", fast_sleep)
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"unfinished"}}]}\n\n')
        handler.close_connection = True
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        visible = []
        with pytest.raises(ApiPlannerError) as caught:
            planner.request_turn_stream(task="Inspect firmware", tool_results=(), allowed_tools=("read_file",),
                run_id="invalid-streams", turn=1, on_summary=visible.append)
    assert caught.value.safe_code == "PROVIDER_STREAM_INVALID"
    # Primary gets two retries. The repair's 120-second window cannot fit a
    # second full 120-second first-event allowance plus backoff.
    assert len(bodies) == planner._request_attempts["used"] == 4
    assert planner._request_attempts["used"] <= planner._request_attempts["limit"] == 6
    assert visible == []
    assert planner._native_messages == []
    assert planner.to_safe_metadata()["initial_failure_code"] == "PROVIDER_STREAM_INVALID"
    assert planner.to_safe_metadata()["recovery_failure_code"] == "PROVIDER_STREAM_INVALID"


@pytest.mark.parametrize("provider,model,options,expected", [
    ("openrouter", MODEL, {}, {"effort": "low"}),
    ("openrouter", MODEL, {"providerOptions": {"openrouter": {"reasoning": {"effort": "high"}}}}, {"effort": "high"}),
    ("openrouter", MODEL, {"body": {"reasoning": {"enabled": True}}}, None),
    ("openrouter", "unknown-reasoning-model", {}, None),
    ("deepseek", "deepseek-reasoner", {}, None),
])
def test_repair_reasoning_is_supported_and_preserves_explicit_configuration(tmp_path, provider, model, options, expected):
    reg = registry(tmp_path)
    reg.configure_provider(provider, {"api_key": "fixture", "options": options})
    payload, _ = OpenCodeProvider(reg, provider)._payload(ModelRequest(prompt="repair", provider_id=provider,
        model_id=model, metadata={"model_output_repair": True}))
    actual = ((payload.get("provider_options") or {}).get("openrouter") or {}).get("reasoning")
    assert actual == expected


def test_repair_output_is_bounded_by_model_and_remaining_job_tokens(tmp_path, monkeypatch):
    from backend.model_router import opencode_catalog
    reg = registry(tmp_path)
    planner = RouterProductPlanner(ModelRouterService(registry=reg), provider_id="openrouter", model_id=MODEL)
    planner._metadata["requested_output_tokens"] = 8192
    monkeypatch.setattr(opencode_catalog, "model_data", lambda *_: {"limit": {"output": 10000}})
    assert planner._response_allowance("MODEL_OUTPUT_TRUNCATED") == 10000
    planner.bind_job_budget({"consumed": {"output_tokens": 90000}, "limits": {
        "output_tokens": 100000, "requests": 20, "input_bytes": 1000000}}, lambda _: None)
    assert planner._response_allowance("MODEL_OUTPUT_TRUNCATED") == 7952


def test_failure_contract_and_message_preserve_initial_and_terminal_causes():
    run = ProductAgentRun("run", "project", "openrouter", status="failed", classification="AGENT_TURN_LIMIT_EXCEEDED")
    apply_provider_diagnostics(run, {"initial_failure_code": "MODEL_OUTPUT_TRUNCATED",
        "recovery_failure_code": "AGENT_TURN_LIMIT_EXCEEDED", "last_repair_outcome": "timed_out",
        "initial_attempt_diagnostics": {"finish_reason": "length", "reasoning_tokens": 8192, "private_prompt": "SECRET"}})
    payload = run.to_safe_dict()
    assert payload["failure"]["primary_code"] == "MODEL_OUTPUT_TRUNCATED"
    assert payload["failure"]["terminal_stop_code"] == "AGENT_TURN_LIMIT_EXCEEDED"
    assert "truncated" in payload["assistant_message"] and "local time limit" in payload["assistant_message"]
    assert payload["build_verified"] is False
    assert "SECRET" not in json.dumps(payload)


def test_repair_deadline_preserves_original_failure_and_reaps_worker(tmp_path, monkeypatch):
    from backend.agent_runtime import router_planner
    monkeypatch.setattr(router_planner, "MODEL_REPAIR_SECONDS", 0.5)
    monkeypatch.setattr(router_planner, "MIN_MODEL_REPAIR_SECONDS", 0.1)
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) == 1:
            reply(handler, {"reasoning_content": "thinking"}, finish="length")
        else:
            time.sleep(1)
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url, turn_timeout_s=20)
        with pytest.raises(ApiPlannerError) as caught:
            request(planner)
    assert caught.value.safe_code == "AGENT_TURN_LIMIT_EXCEEDED"
    diagnostics = planner.to_safe_metadata()
    assert diagnostics["initial_failure_code"] == "MODEL_OUTPUT_TRUNCATED"
    assert diagnostics["recovery_failure_code"] == "AGENT_TURN_LIMIT_EXCEEDED"
    assert diagnostics["last_repair_outcome"] == "timed_out"
    assert diagnostics["worker_exit_code"] is not None
    assert diagnostics["worker_cleanup_incomplete"] is False
    assert diagnostics["physical_request_count"] == 2


def test_user_cancellation_reaps_real_worker_without_replaying(tmp_path):
    started = threading.Event()
    release = threading.Event()
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        started.set()
        release.wait(30)
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        async def cancel():
            task = asyncio.create_task(planner.request_turn_async(task="Inspect firmware", tool_results=(),
                allowed_tools=("read_file",), run_id="cancel-output", turn=1))
            try:
                assert await asyncio.to_thread(started.wait, 20)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        asyncio.run(cancel())
    assert len(bodies) == 1
    assert planner.to_safe_metadata()["worker_exit_code"] is not None
    assert planner.to_safe_metadata()["worker_cleanup_incomplete"] is False
    assert planner._native_messages == []
    assert planner.to_safe_metadata()["last_attempt_succeeded"] is False


def test_generation_reserves_repair_window_and_preserves_short_review_deadline(tmp_path):
    from tests.unit.test_router_product_planner import Adapter, PLAN, registry as openai_registry
    from backend.model_router.models import ModelResponse
    class Truncated(Adapter):
        async def generate(self, value):
            if not self.requests:
                self.requests.append(value)
                return ModelResponse("unfinished", self.provider_id, value.model_id, truncated=True)
            return await super().generate(value)
    adapter = Truncated([PLAN, PLAN])
    router = ModelRouterService(registry=openai_registry(tmp_path), providers={"openai": adapter})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")
    planner.bind_deadline(time.monotonic() + 1000)
    started = time.monotonic()
    plan = request(planner)
    primary, repair = adapter.requests[:2]
    assert 175 < primary.execution_context.deadline - started <= 180
    assert 119 < repair.execution_context.deadline - time.monotonic() <= 120
    assert repair.execution_context.deadline <= started + 300
    assert repair.max_tokens == 16384
    assert primary.execution_context.attempts is repair.execution_context.attempts
    planner.bind_phase("review")
    call = plan.tool_calls[0]
    request(planner, observations=({"call_id": call.call_id, "tool": call.tool_type,
        "status": "completed", "output": {"path": "src/main.cpp", "content": "fixture"}},), turn=2)
    assert adapter.requests[-1].execution_context.deadline - time.monotonic() <= 60


def test_new_failure_diagnostics_survive_durable_store_reopening(tmp_path):
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    run = ProductAgentRun("run", "project", "openrouter", status="failed", classification="AGENT_TURN_LIMIT_EXCEEDED")
    apply_provider_diagnostics(run, {"initial_failure_code": "MODEL_OUTPUT_TRUNCATED",
        "recovery_failure_code": "AGENT_TURN_LIMIT_EXCEEDED",
        "initial_attempt_diagnostics": {"finish_reason": "length", "reasoning_tokens": 8192,
            "completion_received": True, "requested_output_tokens": 8192},
        "repair_attempt_diagnostics": {"completion_received": False, "worker_exit_code": -1}})
    store = AgentOrchestrationStore(tmp_path / "runs.sqlite3")
    store.save_run("run", "session", "project", "failed", run.to_safe_dict())
    payload = AgentOrchestrationStore(store.path).load_run("run")
    restored = ProductAgentRun("run", "project", "openrouter", status="failed", classification="AGENT_TURN_LIMIT_EXCEEDED")
    apply_provider_diagnostics(restored, payload["provider_diagnostics"])
    assert restored.initial_attempt_diagnostics["reasoning_tokens"] == 8192
    assert restored.repair_attempt_diagnostics["worker_exit_code"] == -1
    assert restored.to_safe_dict()["failure"]["primary_code"] == "MODEL_OUTPUT_TRUNCATED"


def test_real_worker_failure_preserves_changeset_and_continuation_does_not_rewrite(tmp_path):
    from tests.unit.test_tool_runtime import make_runtime
    from backend.agent_runtime.tool_runtime import ProductRuntimeLimits
    bodies = []
    content = "void setup() {}\nvoid loop() {}\n"
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) == 1:
            reply(handler, {"tool_calls": [{"index": 0, "id": "saved-write", "type": "function",
                "function": {"name": "write", "arguments": json.dumps({"path": "src/main.cpp", "content": content})}}]}, finish="tool_calls")
        elif len(bodies) in (2, 3):
            reply(handler, {"reasoning_content": "thinking"}, finish="length")
        elif len(bodies) == 4:
            reply(handler, {"tool_calls": [{"index": 0, "id": "inspect-saved", "type": "function",
                "function": {"name": "read", "arguments": '{"path":"src/main.cpp"}'}}]}, finish="tool_calls")
        else:
            reply(handler, {"content": "The saved source is preserved; firmware compilation has not been performed."})
    runtime, active, changes = make_runtime(tmp_path)
    original = (active / "README.txt").read_bytes()
    with upstream(respond) as url:
        planner = planner_for(tmp_path, url)
        failed = asyncio.run(runtime.run_product_async(task="Create a small source file", planner=planner,
            policy=product_agent_policy(), limits=ProductRuntimeLimits(max_runtime_seconds=900)))
        assert failed.provider_classification == "MODEL_OUTPUT_TRUNCATED"
        assert failed.change_set_id is not None
        assert failed.created_file_count == failed.tool_execution_count == 1
        assert failed.active_workspace_unchanged
        draft = changes.get(failed.change_set_id)
        assert draft.review_required
        assert (Path(draft.staging_root) / "src/main.cpp").read_text() == content
        resumed = asyncio.run(runtime.run_product_async(task="Inspect and preserve the saved source", planner=planner_for(tmp_path, url),
            policy=product_agent_policy(), limits=ProductRuntimeLimits(max_runtime_seconds=900),
            retry_change_set_id=failed.change_set_id))
    assert resumed.change_set_id is not None
    assert resumed.tool_execution_count == 1
    assert "compilation has not been performed" in resumed.assistant_message
    assert len(bodies) == 5
    assert "RECOVERED STAGED WORK" in json.dumps(bodies[3]["messages"])
    assert any(message.get("tool_call_id") == "inspect-saved" and "void setup()" in message["content"]
        for message in bodies[4]["messages"])
    assert not (active / "src/main.cpp").exists()
    assert (active / "README.txt").read_bytes() == original
    assert (Path(changes.get(resumed.change_set_id).staging_root) / "src/main.cpp").read_text() == content
