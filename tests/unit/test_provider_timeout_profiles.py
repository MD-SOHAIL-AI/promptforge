"""Provider timing selection and real worker deadline/progress regressions."""
import asyncio
import json
import time
from contextlib import aclosing

import pytest

from backend.contracts.transport import TransportPolicy
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.normalization import CanonicalStreamAssembler
from backend.model_router.providers.opencode import OpenCodeProvider, _engine_error
from backend.model_router.request_recovery import retry_wait, text_stream
from backend.model_router.transport_policy import provider_transport_policy, request_transport_policy
from backend.services.llm_service import LLMProvider, LLMProviderError, LLMTimeoutError
from tests.unit.test_opencode_engine import registry, upstream


def test_profiles_cover_queueing_loading_reasoning_and_explicit_override():
    free_policy = provider_transport_policy("openrouter", "cohere/north-mini-code:free")
    assert free_policy.first_event_seconds == 120 and free_policy.idle_seconds == 120
    assert provider_transport_policy("nvidia_nim", phase="generation").idle_seconds == 120
    assert provider_transport_policy("nvidia_nim", phase="review").idle_seconds == 60
    assert provider_transport_policy("ollama").total_seconds == 600
    assert provider_transport_policy("lmstudio").first_event_seconds == 180
    assert provider_transport_policy("deepseek").idle_seconds == 120
    assert provider_transport_policy("openai", reasoning=True).total_seconds == 600
    assert provider_transport_policy("cerebras").idle_seconds == 120
    assert provider_transport_policy("custom-provider") == TransportPolicy()
    explicit = TransportPolicy(first_event_seconds=7, idle_seconds=2, total_seconds=10)
    request = ModelRequest(prompt="hello", provider_id="ollama",
        execution_context=RequestExecutionContext(transport_policy=explicit))
    assert request_transport_policy(request) is explicit


def headers(handler):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("x-request-id", "timeout-test-request")
    handler.end_headers()
    handler.wfile.flush()


def packet(handler, delta, finish=None):
    handler.wfile.write(("data: " + json.dumps({"choices": [{"index": 0, "delta": delta,
        **({"finish_reason": finish} if finish else {})}]}) + "\n\n").encode())
    handler.wfile.flush()


def consume(reg, context):
    return consume_stream(reg, "deepseek", ModelRequest(
        prompt="hello", model_id="deepseek-chat", execution_context=context))


def consume_stream(reg, provider_id, request):
    async def collect():
        assembler = CanonicalStreamAssembler()
        async with aclosing(OpenCodeProvider(reg, provider_id).generate_event_stream(request)) as events:
            async for event in events:
                assembler.add(event)
        return assembler.response(provider_id=provider_id, model_id=request.model_id)
    return asyncio.run(collect())


def test_fast_headers_slow_first_output_uses_first_event_window(tmp_path):
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        headers(handler)
        time.sleep(.5)  # Longer than idle; valid before generation begins.
        packet(handler, {"content": "OK"})
        packet(handler, {}, "stop")
        handler.wfile.write(b"data: [DONE]\n\n"); handler.wfile.flush()
    reg = registry(tmp_path)
    context = RequestExecutionContext(transport_policy=TransportPolicy(
        first_event_seconds=5, idle_seconds=.2, total_seconds=7))
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "fixture", "base_url": url})
        assert consume(reg, context).content == "OK"
    assert len(bodies) == context.attempts["used"] == 1
    assert context.diagnostics["completion_received"]
    assert "policy" not in bodies[0] and "total_stage" not in json.dumps(bodies)


@pytest.mark.parametrize("started", [False, True])
def test_heartbeats_do_not_extend_meaningful_deadline(tmp_path, started):
    def respond(handler, body):
        headers(handler)
        if started:
            packet(handler, {"content": "partial"})
        try:
            for _ in range(60):
                handler.wfile.write(b": heartbeat\n\n"); handler.wfile.flush(); time.sleep(.05)
        except (BrokenPipeError, ConnectionResetError):
            pass
    reg = registry(tmp_path)
    context = RequestExecutionContext(transport_policy=TransportPolicy(
        first_event_seconds=2, idle_seconds=.25, total_seconds=5))
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "fixture", "base_url": url})
        with pytest.raises(LLMTimeoutError) as caught:
            consume(reg, context)
    assert caught.value.details["timeout_stage"] == ("stream_idle" if started else "first_event")
    assert context.diagnostics["response_headers_received"]
    assert context.diagnostics["worker_exit_code"] is not None
    assert context.diagnostics["completion_received"] is False
    assert (context.diagnostics["first_event_ms"] is not None) == started
    assert context.attempts["used"] == 1


@pytest.mark.parametrize("provider_id,model_id", [("deepseek", "deepseek-chat"), ("salad-cloud", "test-model")])
def test_streaming_tool_arguments_are_progress_before_complete_tool(tmp_path, provider_id, model_id):
    def respond(handler, body):
        headers(handler)
        packet(handler, {"content": "Preparing tool"})
        fragments = ['{"path":', '"main.cpp",', '"content":', '"hello', ' world', '"}']
        for index, text in enumerate(fragments):
            time.sleep(.1)
            call = {"index": 0, "function": {"arguments": text}}
            if index == 0:
                call.update(id="call-1", type="function")
                call["function"]["name"] = "write"
            packet(handler, {"tool_calls": [call]})
        packet(handler, {}, "tool_calls")
        handler.wfile.write(b"data: [DONE]\n\n"); handler.wfile.flush()
    reg = registry(tmp_path)
    context = RequestExecutionContext(transport_policy=TransportPolicy(
        first_event_seconds=5, idle_seconds=.25, total_seconds=7))
    with upstream(respond) as url:
        reg.configure_provider(provider_id, {"api_key": "fixture", "base_url": url})
        request = ModelRequest(prompt="write", model_id=model_id, execution_context=context,
            metadata={"forgex_native_tools": [{"type": "function", "function": {"name": "write",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}}}]})
        response = consume_stream(reg, provider_id, request)
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["arguments"] == {"path": "main.cpp", "content": "hello world"}
    assert context.diagnostics["tool_event_count"] == 1
    assert context.diagnostics["completion_received"]


@pytest.mark.parametrize("stage,code", [("turn_deadline", "AGENT_TURN_LIMIT_EXCEEDED"),
                                        ("job_deadline", "AGENT_RUNTIME_LIMIT_EXCEEDED")])
def test_parent_deadline_is_distinct_and_expired_request_does_not_spawn(tmp_path, monkeypatch, stage, code):
    context = RequestExecutionContext(deadline_kind=stage,
        transport_policy=TransportPolicy(first_event_seconds=5, idle_seconds=5, total_seconds=7))
    reg = registry(tmp_path)
    reg.configure_provider("deepseek", {"api_key": "fixture"})
    # Guarantee headers precede the parent deadline. Real worker startup on a
    # constrained host may legitimately consume the entire short allowance;
    # the other HTTP fixtures exercise actual child/transport timing.
    async def headed_worker(*args, **kwargs):
        output, errors = asyncio.StreamReader(), asyncio.StreamReader()
        output.feed_data(b'{"type":"transport","status":200}\n')
        exited = asyncio.Event()
        class Input:
            def write(self, _): pass
            async def drain(self): pass
            def close(self): pass
        class Child:
            returncode = None
            stdin, stdout, stderr = Input(), output, errors
            def kill(self):
                self.returncode = 1
                output.feed_eof()
                errors.feed_eof()
                exited.set()
            async def wait(self):
                await exited.wait()
                return self.returncode
        return Child()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", headed_worker)
    context.deadline = time.monotonic() + .1
    with pytest.raises(LLMTimeoutError) as caught:
        consume(reg, context)
    assert caught.value.code == code and caught.value.retryable is False
    assert caught.value.details["timeout_stage"] == stage
    assert context.diagnostics["response_headers_received"]
    assert asyncio.run(retry_wait(ModelRequest(prompt="hello", execution_context=context), caught.value, 1)) is False
    if context.attempts["used"]:
        assert context.diagnostics["worker_exit_code"] is not None
    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("Expired requests must not start a child")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    context.deadline = time.monotonic() - 1
    used = context.attempts["used"]
    with pytest.raises(LLMTimeoutError) as caught:
        consume(reg, context)
    assert caught.value.code == code
    assert context.attempts["used"] == used
    worker_error = _engine_error({"timeout_stage": stage}, True, time.monotonic())
    assert worker_error.code == code and worker_error.retryable is False


def test_retry_reserves_provider_window_instead_of_global_default():
    request = ModelRequest(prompt="hello", provider_id="ollama", model_id="local-model",
        execution_context=RequestExecutionContext(deadline=time.monotonic() + 150))
    error = LLMProviderError("Overloaded", provider=LLMProvider.OPENAI, status_code=503,
                             retryable=True, details={"retry_after_ms": 0})
    assert asyncio.run(retry_wait(request, error, 1)) is False


def test_legacy_text_stream_respects_explicit_first_event_override():
    async def source(request):
        await asyncio.sleep(.12)
        yield "OK"
    async def generate(request):
        pytest.fail("No fallback generation expected")
    request = ModelRequest(prompt="hello", provider_id="openrouter", model_id="model:free",
        metadata={"forgex_transport_policy": TransportPolicy(first_event_seconds=.3, idle_seconds=.05, total_seconds=1)})
    async def run():
        return [text async for text in text_stream(request, source, generate)]
    assert asyncio.run(run()) == ["OK"]
