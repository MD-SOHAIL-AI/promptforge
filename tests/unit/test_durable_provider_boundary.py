"""Exercise durable budgets through the real adapter and HTTP service."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.providers.openai_provider import OpenAIProvider
from backend.model_router.providers.openrouter import OpenRouterProvider
from backend.model_router.providers.anthropic import AnthropicProvider
from backend.model_router.providers.cerebras import CerebrasProvider
from backend.model_router.providers.compatible import CompatibleProvider
from backend.model_router.providers.deepseek import DeepSeekProvider
from backend.model_router.providers.gemini_provider import GeminiProvider
from backend.model_router.providers.groq import GroqProvider
from backend.model_router.providers.lmstudio import LMStudioProvider
from backend.model_router.providers.nvidia_nim import NvidiaNimProvider, NvidiaNimService
from backend.model_router.providers.ollama import OllamaProvider
import backend.model_router.providers.ollama as ollama_module
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.storage import ProviderSettingsStorage
from backend.services.llm_service import (
    AnthropicService, GeminiService, OpenAIService, OpenAIResponsesService, OpenRouterService,
)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("budget_mode", ["success", "exhausted", "repair"])
@pytest.mark.parametrize("provider_type,service_type,protocol", [
    (OpenAIProvider, OpenAIService, "openai-completions"),
    (OpenRouterProvider, OpenRouterService, "openai-completions"),
    (DeepSeekProvider, OpenAIService, "openai-completions"),
    (GroqProvider, OpenAIService, "openai-completions"),
    (CerebrasProvider, OpenAIService, "openai-completions"),
    (NvidiaNimProvider, NvidiaNimService, "openai-completions"),
    (LMStudioProvider, OpenAIService, "openai-completions"),
    (AnthropicProvider, AnthropicService, "anthropic-messages"),
    (GeminiProvider, GeminiService, "gemini"),
    (OllamaProvider, None, "ollama"),
    (CompatibleProvider, OpenAIService, "openai-completions"),
    (CompatibleProvider, OpenAIResponsesService, "openai-responses"),
    (CompatibleProvider, AnthropicService, "anthropic-messages"),
])
def test_durable_budget_callback_stays_local_and_is_persisted_before_http(
    tmp_path, monkeypatch, stream, provider_type, service_type, protocol, budget_mode,
):
    models = ProviderRegistry(ProviderSettingsStorage(
        tmp_path / "models.json", credential_store=MemoryCredentialStore(),
    ))
    if provider_type is CompatibleProvider:
        provider_id = "custom"
        models.register_custom_provider(provider_id=provider_id, display_name="Test route",
            protocol=protocol, base_url="https://provider.invalid/v1",
            default_model="gpt-4.1-mini", api_key="test")
    else:
        provider_id = provider_type.provider_id
        models.configure_provider(provider_id, {"api_key": "test", "enabled": True})
    plan = {"version": "forgex.toolplan.v2", "summary": "Inspect firmware",
            "tool_calls": [{"id": "read-1", "type": "read_file",
                            "arguments": {"path": "src/main.cpp"}}], "final": False}
    persisted, sent = [], []

    def respond(request):
        assert len(persisted) == 1
        payload = json.loads(request.content)
        assert "forgex_run_budget" not in json.dumps(payload)
        assert "_persist" not in json.dumps(payload)
        sent.append(payload)
        content = json.dumps({"invalid_plan": True} if budget_mode == "repair" else plan)
        if protocol == "anthropic-messages":
            envelope = {"content": [{"type": "text", "text": content}], "stop_reason": "end_turn"}
            packets = [{"type": "content_block_delta", "index": 0,
                        "delta": {"type": "text_delta", "text": content}},
                       {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                       {"type": "message_stop"}]
        elif protocol == "gemini":
            envelope = {"candidates": [{"content": {"parts": [{"text": content}]}, "finishReason": "STOP"}]}
            packets = [envelope]
        elif protocol == "openai-responses":
            envelope = {"status": "completed", "output": [{"type": "message",
                "content": [{"type": "output_text", "text": content}]}]}
            packets = [{"type": "response.output_text.delta", "delta": content},
                       {"type": "response.completed", "response": {"status": "completed"}}]
        elif protocol == "ollama":
            envelope = {"message": {"content": content}, "done": True}
            if stream:
                return httpx.Response(200, text=json.dumps(envelope) + "\n")
            packets = []
        else:
            envelope = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
            packets = [
                {"choices": [{"delta": {"content": content}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
        if stream:
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                text="".join("data: " + json.dumps(p) + "\n\n" for p in packets)
                     + "data: [DONE]\n\n")
        return httpx.Response(200, json=envelope)

    async def execute():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            provider = (provider_type(models, provider_id, protocol)
                        if provider_type is CompatibleProvider else provider_type(models))
            if provider_type is OllamaProvider:
                monkeypatch.setattr(ollama_module, "httpx", SimpleNamespace(
                    AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(respond), **kwargs),
                    TimeoutException=httpx.TimeoutException, RequestError=httpx.RequestError))
            else:
                provider._service = lambda model_id=None: service_type(
                    api_key="test", model=model_id or "test-model", client=client,
                )
            router = ModelRouterService(registry=models, providers={provider_id: provider})
            planner = RouterProductPlanner(router, provider_id=provider_id,
                model_id="gpt-4.1-mini", max_tokens=1024)
            planner.bind_job_budget({"consumed": {"output_tokens": 1024} if budget_mode == "exhausted" else {}, "limits": {
                "requests": 10, "output_tokens": 100_000 if budget_mode == "success" else 1024, "input_bytes": 100_000,
            }}, persisted.append)
            async def turn():
                planner.bind_phase("intake")  # This matrix isolates physical accounting; generation reserve is tested separately.
                return await planner.request_turn_async(stream=stream,
                    task="Inspect firmware", tool_results=(), allowed_tools=("read_file",),
                    run_id="durable-run", turn=1)
            if budget_mode == "success":
                result = await turn()
                assert result.summary == plan["summary"]
            else:
                with pytest.raises(ApiPlannerError) as failure:
                    await turn()
                assert failure.value.safe_code == "AGENT_PROVIDER_BUDGET_EXCEEDED"
                assert planner.to_safe_metadata()["last_failure_origin"] == "job_budget"
                assert planner.to_safe_metadata()["last_failure_code"] == "AGENT_PROVIDER_BUDGET_EXCEEDED"
                expected = 0 if budget_mode == "exhausted" else 1
                assert planner.to_safe_metadata()["outbound_request_count"] == expected
                assert planner.to_safe_metadata()["physical_request_count"] == expected
                assert planner.to_safe_metadata()["request_reached_provider"] is (expected > 0)
                if not expected:
                    assert persisted == sent == []
                    return
            assert planner.budget_snapshot()["requests"] == 1
            assert planner.to_safe_metadata()["physical_request_count"] == 1
            assert planner.to_safe_metadata()["request_reached_provider"] is True

    asyncio.run(execute())
    if budget_mode != "exhausted":
        assert len(sent) == 1
        assert persisted[0]["requests"] == 1
        assert persisted[0]["output_tokens"] == 1024
