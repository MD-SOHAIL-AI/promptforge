"""Actual planner -> router -> Node engine -> HTTP -> tools -> next turn."""
import asyncio
import json

import pytest

from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.tool_contracts import ToolCall, parse_tool_name
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import ToolPermissionPolicy
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.router import ModelRouterService
from backend.model_router.usage import UsageTracker
from backend.services.llm_service import LLMProviderError
from tests.unit.test_opencode_engine import registry, upstream


@pytest.mark.parametrize("provider_id", ["openrouter", "mistral"])
def test_real_planner_and_worker_continue_after_multiple_tool_batches(tmp_path, monkeypatch, provider_id):
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(tmp_path / "diagnostics.jsonl"))
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src/main.cpp").write_text("void setup() {}\nvoid loop() {}\n")
    (project / "platformio.ini").write_text("[env:esp32dev]\nplatform = espressif32\n")
    executor = ForgeXToolExecutor(write_root=project, policy=ToolPermissionPolicy())
    original_files = {str(path.relative_to(project)): path.read_bytes()
                      for path in project.rglob("*") if path.is_file()}
    bodies = []
    batches = [
        [("call-list", "list", {})],
        [("call-main", "read", {"path": "src/main.cpp"}),
         ("call-config", "read", {"path": "platformio.ini"})],
        [("call-list-again", "list", {"path": "src"})],
        [("call-read-again", "read", {"path": "src/main.cpp", "offset": 1, "limit": 1})],
    ]

    def respond(handler, body):
        turn = len(bodies)
        bodies.append(body)
        delta = {"content": "Checked both source and configuration."}
        finish = "stop"
        if turn < len(batches):
            delta = {"tool_calls": [{"index": index, "id": identifier, "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)}}
                for index, (identifier, name, arguments) in enumerate(batches[turn])]}
            finish = "tool_calls"
        event = {"id": "fixture", "object": "chat.completion.chunk", "created": 1,
            "model": body["model"], "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.end_headers()
        handler.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
        handler.wfile.flush()

    reg = registry(tmp_path)
    model_id = ("nvidia/nemotron-3-ultra-550b-a55b:free" if provider_id == "openrouter"
                else reg.default_model(provider_id))
    assert reg.model_capabilities(provider_id, model_id).native_tools
    observations = []
    executed_arguments = {}
    with upstream(respond) as url:
        reg.configure_provider(provider_id, {"api_key": "fixture", "base_url": url})
        router = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path / "usage.jsonl"))
        planner = RouterProductPlanner(router, provider_id=provider_id, model_id=model_id)

        async def run():
            for turn in range(1, 6):
                plan = await planner.request_turn_async(task="Inspect the project without changing it.",
                    tool_results=tuple(observations), allowed_tools=("list_files", "read_file"),
                    run_id="history-regression", turn=turn)
                if turn == 5:
                    assert plan.final
                    assert not plan.tool_calls
                    assert plan.summary == "Checked both source and configuration."
                else:
                    assert not plan.final
                    for call in plan.tool_calls:
                        executed_arguments[call.call_id] = dict(call.arguments)
                        output = executor.execute(ToolCall(parse_tool_name(call.tool_type), call.arguments))
                        observations.append({"call_id": call.call_id, "tool": call.tool_type,
                            "status": "completed", "output": output})
        asyncio.run(run())

    assert len(bodies) == 5
    assert len(observations) == 5
    assert all(body["model"] == model_id for body in bodies)
    # Actual tool output and original opaque IDs survive every later request.
    for turn, body in enumerate(bodies[1:], start=1):
        assistant_calls = [call for message in body["messages"] if message["role"] == "assistant"
                           for call in message.get("tool_calls", [])]
        expected = [item for batch in batches[:turn] for item in batch]
        assert [call["id"] for call in assistant_calls] == [item[0] for item in expected]
        assert [call["function"]["name"] for call in assistant_calls] == [item[1] for item in expected]
        assert [json.loads(call["function"]["arguments"]) for call in assistant_calls] == [executed_arguments[item[0]] for item in expected]
        results = [message for message in body["messages"] if message["role"] == "tool"]
        assert [message["tool_call_id"] for message in results] == [item[0] for item in expected]
        assert "src/main.cpp" in results[0]["content"]
    assert {str(path.relative_to(project)): path.read_bytes()
            for path in project.rglob("*") if path.is_file()} == original_files
    assert planner.budget_snapshot()["requests"] == 5


@pytest.mark.parametrize("provider_id", ["openrouter", "mistral"])
def test_invalid_history_crosses_the_actual_worker_boundary_without_http_or_retry(tmp_path, monkeypatch, provider_id):
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(tmp_path / "diagnostics.jsonl"))
    sent = []
    def respond(handler, body):
        sent.append(body)
        handler.send_response(500)
        handler.end_headers()
    reg = registry(tmp_path)
    model_id = ("nvidia/nemotron-3-ultra-550b-a55b:free" if provider_id == "openrouter"
                else reg.default_model(provider_id))
    with upstream(respond) as url:
        reg.configure_provider(provider_id, {"api_key": "fixture", "base_url": url})
        router = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path / "usage.jsonl"))
        context = RequestExecutionContext()
        with pytest.raises(LLMProviderError) as caught:
            asyncio.run(router.generate_model(ModelRequest(prompt="Inspect only.", provider_id=provider_id,
                model_id=model_id, execution_context=context,
                metadata={"forgex_messages": [{"role": "assistant", "content": "",
                    "tool_calls": [{"id": "call-1", "arguments": {}}]}]})))
    assert caught.value.code == "MODEL_MESSAGE_HISTORY_INVALID"
    assert caught.value.retryable is False
    assert caught.value.details["category"] == "invalid_request"
    assert caught.value.details["request_reached_provider"] is False
    assert len(router.runtime.last_decision.attempts) == 1
    assert sent == []
