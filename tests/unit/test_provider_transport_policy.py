"""Exercise transport termination and deadlines with actual async HTTP streams."""
import asyncio
import json
import time

import httpx
import pytest

from backend.contracts.transport import TransportPolicy
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.providers.nvidia_nim import NvidiaNimService
from backend.model_router.request_recovery import retry_wait
from backend.services.llm_service import LLMRequest, LLMTimeoutError, OpenAIService


def packet(data):
    return ("data: " + json.dumps(data) + "\n\n").encode()


class ScriptedStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.read_after_script = False

    async def __aiter__(self):
        for delay, data in self.chunks:
            await asyncio.sleep(delay)
            yield data
        self.read_after_script = True
        await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def test_done_closes_a_held_open_response_and_preserves_finish_and_usage():
    stream = ScriptedStream([
        (0, packet({"choices": [{"delta": {"content": "answer"}}]})),
        (0, packet({"choices": [{"delta": {}, "finish_reason": "length"}]})),
        (0, packet({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 4}})),
        (0, b"data: [DONE]\n\n"),
    ])
    sent, progress = [], []
    def respond(request):
        sent.append(request)
        return httpx.Response(200, headers={"x-request-id": "safe-upstream-id"}, stream=stream)
    async def scenario():
        context = RequestExecutionContext(recovery_hook=progress.append,
            transport_policy=TransportPolicy(first_event_seconds=.1, idle_seconds=.1, total_seconds=.3))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            service = OpenAIService(api_key="secret-key", model="model", client=client)
            service.bind_execution_context(context, provider_id="openai", identity={"run_id": "r", "turn_id": "r:turn-1"})
            events = [event async for event in service.generate_event_stream(LLMRequest(prompt="secret-prompt"))]
            assert events[-1].finish_reason == "length"
            assert events[-1].usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
            assert context.diagnostics["completion_received"] is True
            assert context.diagnostics["provider_request_id"] == "safe-upstream-id"
            assert len(progress) == 1
            assert "secret-prompt" not in json.dumps(progress) and "secret-key" not in json.dumps(progress)
            assert "transport_policy" not in sent[0].content.decode()
    asyncio.run(scenario())
    assert len(sent) == 1 and stream.closed and not stream.read_after_script


def test_productive_stream_can_outlive_the_old_total_timeout():
    stream = ScriptedStream([(0.025, packet({"choices": [{"delta": {"content": "a"}}]})) for _ in range(6)]
                            + [(0, b"data: [DONE]\n\n")])
    async def scenario():
        context = RequestExecutionContext(transport_policy=TransportPolicy(
            first_event_seconds=.2, idle_seconds=.15, total_seconds=1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            service = OpenAIService(api_key="test", model="model", client=client, timeout_s=.05)
            service.bind_execution_context(context, provider_id="openai")
            events = [event async for event in service.generate_event_stream(LLMRequest(prompt="test"))]
            assert "".join(event.text or "" for event in events) == "aaaaaa"
            assert context.diagnostics["duration_ms"] >= 120
            assert context.diagnostics["completion_received"] is True
    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["response.completed", "response.done"])
def test_responses_terminal_marker_preserves_usage_without_waiting_for_eof(terminal):
    stream = ScriptedStream([(0, packet({"type":"response.output_text.delta", "delta":"answer"})),
        (0, packet({"type":terminal,"response":{"usage":{"input_tokens":2,"output_tokens":5}}}))])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            service = OpenAIService(api_key="test", model="m", client=client, timeout_s=.1)
            events = [event async for event in service.generate_event_stream(LLMRequest(prompt="test"))]
            assert events[-1].usage == {"input_tokens":2,"output_tokens":5,"total_tokens":7}
    asyncio.run(scenario())
    assert stream.closed and not stream.read_after_script


@pytest.mark.parametrize("started", [False, True])
def test_semantic_watchdog_ignores_heartbeats_and_retains_response_metadata(started):
    chunks = [(0, packet({"choices": [{"delta": {"content": "partial"}}]}))] if started else []
    chunks += [(0.015, b": heartbeat\n\n") for _ in range(10)]
    stream = ScriptedStream(chunks)
    async def scenario():
        context = RequestExecutionContext(transport_policy=TransportPolicy(
            first_event_seconds=.07, idle_seconds=.07, total_seconds=.5))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, headers={"x-request-id": "upstream-123"}, stream=stream))) as client:
            service = NvidiaNimService(api_key="test", model="selected", client=client)
            service.bind_execution_context(context, provider_id="nvidia_nim")
            with pytest.raises(LLMTimeoutError) as caught:
                _ = [event async for event in service.generate_event_stream(LLMRequest(prompt="test"))]
            error = caught.value
            assert error.details["timeout_stage"] == ("stream_idle" if started else "first_event")
            assert error.details["http_status"] == 200
            assert error.details["request_reached_provider"] is True
            assert error.details["response_headers_received"] is True
            assert error.details["provider_request_id"] == "upstream-123"
            assert error.details["completion_received"] is False
            assert (error.details["first_event_ms"] is not None) is started
            assert "nvidia_nim" in str(error) and "OPENAI" not in str(error)
    asyncio.run(scenario())
    assert stream.closed


def test_parent_deadline_and_user_cancellation_remain_distinct():
    async def scenario(cancel):
        stream = ScriptedStream([])
        context = RequestExecutionContext(deadline=time.monotonic() + (.08 if not cancel else 1),
            transport_policy=TransportPolicy(first_event_seconds=1, idle_seconds=1, total_seconds=2))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            service = OpenAIService(api_key="test", model="m", client=client)
            service.bind_execution_context(context, provider_id="openai")
            async def consume():
                return [event async for event in service.generate_event_stream(LLMRequest(prompt="test"))]
            task = asyncio.create_task(consume())
            if cancel:
                await asyncio.sleep(.02)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(LLMTimeoutError) as caught:
                    await task
                assert caught.value.code == "AGENT_RUNTIME_LIMIT_EXCEEDED"
                assert caught.value.details["timeout_stage"] == "job_deadline"
                assert caught.value.retryable is False
        assert stream.closed
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_long_timeout_defers_to_durable_recovery_without_another_physical_attempt():
    context = RequestExecutionContext(attempts={"used": 1, "limit": 4},
                                      transport_policy=TransportPolicy())
    request = ModelRequest(prompt="test", execution_context=context)
    error = LLMTimeoutError("first event timeout", provider=OpenAIService.provider,
        retryable=True, details={"timeout_stage": "first_event", "duration_ms": 120000})
    assert asyncio.run(retry_wait(request, error, 1)) is False
    assert context.attempts["used"] == 1


def test_turn_deadline_does_not_claim_that_the_job_budget_is_exhausted():
    async def scenario():
        context = RequestExecutionContext(deadline=time.monotonic() + .06, deadline_kind="turn_deadline",
            transport_policy=TransportPolicy(first_event_seconds=.5, idle_seconds=.5, total_seconds=1))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=ScriptedStream([])))) as client:
            service = OpenAIService(api_key="test", model="m", client=client)
            service.bind_execution_context(context, provider_id="openai")
            with pytest.raises(LLMTimeoutError) as caught:
                _ = [event async for event in service.generate_event_stream(LLMRequest(prompt="test"))]
            assert caught.value.code == "AGENT_TURN_LIMIT_EXCEEDED"
            assert caught.value.details["timeout_stage"] == "turn_deadline"
            assert await retry_wait(ModelRequest(prompt="test",execution_context=context), caught.value, 1) is False
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True])
def test_transport_policy_rejects_invalid_deadlines(value):
    with pytest.raises(ValueError):
        TransportPolicy(idle_seconds=value)
