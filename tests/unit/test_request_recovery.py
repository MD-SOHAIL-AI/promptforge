"""Actual adapters and HTTP services share retry/repair accounting."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.providers.openai_provider import OpenAIProvider
from backend.model_router.providers.openrouter import OpenRouterProvider
from backend.model_router.providers.nvidia_nim import NvidiaNimProvider, NvidiaNimService
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.storage import ProviderSettingsStorage
from backend.services.llm_service import OpenAIService, OpenRouterService, LLMConfigurationError
from backend.model_router.models import ModelRequest
from backend.model_router import request_recovery


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("adapter,service", [(OpenAIProvider, OpenAIService), (OpenRouterProvider, OpenRouterService), (NvidiaNimProvider, NvidiaNimService)])
@pytest.mark.parametrize("scenario,expected_count", [("success", 2), ("exhausted", 2), ("repair", 3), ("long_wait", 1), ("auth", 1)])
def test_shared_physical_attempt_limit(tmp_path, monkeypatch, stream, adapter, service, scenario, expected_count):
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    registry.configure_provider(adapter.provider_id, {"api_key": "test", "enabled": True})
    reservations, sent, scheduled = [], [], []

    async def delay(seconds):
        assert scheduled[-1]["event_type"] == "model.retry.scheduled"
    monkeypatch.setattr(request_recovery.asyncio, "sleep", delay)

    def respond(request):
        sent.append(json.loads(request.content))
        assert len(reservations) == len(sent)
        assert "forgex_recovery_hook" not in request.content.decode()
        assert "forgex_turn_deadline" not in request.content.decode()
        if scenario == "auth":
            return httpx.Response(401, json={"error": {"message": "Invalid key"}})
        if scenario == "long_wait":
            return httpx.Response(429, headers={"retry-after": "90"}, json={"error": {"message": "Rate limited"}})
        if scenario == "exhausted" or len(sent) < 2:
            return httpx.Response(503, json={"error": {"message": "Service temporarily overloaded"}})
        content = "{invalid json" if scenario == "repair" else json.dumps({"version": "forgex.toolplan.v2",
            "summary": "Inspect firmware", "tool_calls": [{"id": "read", "type": "read_file", "arguments": {"path": "src/main.cpp"}}], "final": False})
        if stream:
            return httpx.Response(200, text='data: '+json.dumps({"choices": [{"delta": {"content": content}}]})+'\n\n'
                +'data: '+json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})+'\n\ndata: [DONE]\n\n')
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = adapter(registry)
            provider._service = lambda model_id=None: service(api_key="test", model=model_id, client=client)
            router = ModelRouterService(registry=registry, providers={adapter.provider_id: provider})
            planner = RouterProductPlanner(router, provider_id=adapter.provider_id, model_id="gpt-4.1-mini")
            planner.bind_job_budget({"consumed": {}, "limits": {"requests": 120, "output_tokens": 245760, "input_bytes": 8000000}}, lambda value: reservations.append(value))
            planner.bind_recovery_events(lambda event: scheduled.append(dict(event)))
            args = dict(task="Inspect firmware", tool_results=(), allowed_tools=("read_file",), run_id="run", turn=1, stream=stream)
            if scenario == "success":
                assert (await planner.request_turn_async(**args)).tool_calls[0].path == "src/main.cpp"
            else:
                with pytest.raises(ApiPlannerError) as failure:
                    await planner.request_turn_async(**args)
                if scenario == "repair":
                    assert failure.value.safe_code == "MODEL_OUTPUT_NOT_JSON"
                    assert planner.to_safe_metadata()["initial_failure_code"] == "MODEL_OUTPUT_NOT_JSON"
            assert planner.to_safe_metadata()["physical_request_count"] == expected_count
    asyncio.run(execute())
    assert len(sent) == len(reservations) == expected_count
    if scenario == "success":
        assert [e["event_type"] for e in scheduled if e["event_type"].startswith("model.retry.")] == ["model.retry.scheduled", "model.retry.started", "model.retry.settled"]
        if stream:
            progress = next(e for e in scheduled if e["event_type"] == "model.request.progress")
            assert progress["response_headers_received"] is True
            assert progress["first_event_ms"] >= 0
            assert "prompt" not in progress and "text" not in progress


def test_local_serialization_failure_has_no_outbound_attempt(tmp_path):
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    provider = OpenAIProvider(registry)
    diagnostics = {"physical_request_count": 0}
    request = ModelRequest(prompt="hello", metadata={"invalid": lambda: None, "forgex_attempt_diagnostics": diagnostics})
    with pytest.raises(LLMConfigurationError) as error:
        asyncio.run(provider.generate(request))
    assert error.value.code == "FORGEX_REQUEST_INVALID"
    assert diagnostics["physical_request_count"] == 0


def test_retry_wait_is_cancellable_and_records_cancellation(monkeypatch):
    from backend.services.llm_service import LLMProviderError, LLMProvider
    events = []
    request = ModelRequest(prompt="hello", metadata={"forgex_managed_recovery": True,
        "forgex_request_attempts": {"used": 1, "limit": 4}, "forgex_recovery_hook": events.append})
    async def cancelled(seconds):
        raise asyncio.CancelledError()
    monkeypatch.setattr(request_recovery.asyncio, "sleep", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(request_recovery.retry_wait(request, LLMProviderError("overload", provider=LLMProvider.OPENAI, status_code=503, retryable=True), 1))
    assert [e["event_type"] for e in events] == ["model.retry.scheduled", "model.retry.cancelled"]


def test_retry_wait_does_not_launch_an_attempt_near_turn_deadline():
    from backend.services.llm_service import LLMProviderError, LLMProvider
    events = []
    request = ModelRequest(prompt="hello", metadata={"forgex_managed_recovery": True,
        "forgex_request_attempts": {"used": 1, "limit": 4},
        "forgex_turn_deadline": time.monotonic() + 10,
        "forgex_recovery_hook": events.append})
    error = LLMProviderError("overload", provider=LLMProvider.OPENAI,
        status_code=503, retryable=True)
    assert asyncio.run(request_recovery.retry_wait(request, error, 1)) is False
    assert events == []


def test_partial_stream_is_discarded_before_retry(tmp_path, monkeypatch):
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    registry.configure_provider("openai", {"api_key": "test", "enabled": True})
    calls, visible = [], []
    async def delay(seconds):
        pass
    monkeypatch.setattr(request_recovery.asyncio, "sleep", delay)
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"discard this partial output"}}]}\n\n'
            raise httpx.ReadError("connection lost")
    def respond(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=BrokenStream())
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Complete answer"}}]}\n\n'
            +'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = OpenAIProvider(registry)
            provider._service = lambda model_id=None: OpenAIService(api_key="test", model=model_id, client=client)
            planner = RouterProductPlanner(ModelRouterService(registry=registry, providers={"openai": provider}), provider_id="openai", model_id="gpt-4.1-mini")
            result = await planner.request_turn_async(task="Explain firmware", tool_results=(), allowed_tools=(), run_id="r", turn=1, on_summary=visible.append)
            assert result.summary == "Complete answer"
    asyncio.run(execute())
    assert len(calls) == 2
    assert visible == ["Complete answer"]
