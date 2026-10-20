import asyncio
import json
import pytest

from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.models import ModelCapabilities, ModelResponse
from backend.services.llm_service import (
    AnthropicService,
    GeminiService,
    LLMRequest,
    OpenAIResponsesService,
    OpenAIService,
)


TOOL = {
    "type": "function",
    "function": {
        "name": "read",
        "description": "Read a file",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "strict": True,
    },
}


class Registry:
    def __init__(self, context_window=None):
        self.context_window = context_window

    def model_is_free(self, _provider, _model):
        return False

    def model_capabilities(self, _provider, _model):
        return ModelCapabilities(
            streaming=False,
            native_tools=True,
            context_window=self.context_window,
        )


class Router:
    def __init__(self, response, *, context_window=None):
        self.registry = Registry(context_window)
        self.response = response
        self.requests = []

    async def generate_model(self, request):
        self.requests.append(request)
        if isinstance(self.response, list):
            return self.response.pop(0)
        return self.response


def test_native_missing_argument_repair_has_precise_schema_and_private_diagnostics(monkeypatch):
    import backend.agent_runtime.router_planner as module
    diagnostics = []
    monkeypatch.setattr(module, "emit_diagnostic_event", lambda **event: diagnostics.append(event))
    router = Router([
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "bad", "name": "write", "arguments": {"path": "private-name.cpp"}},)),
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "good", "name": "write", "arguments": {"path": "private-name.cpp", "content": "private source code"}},)),
    ])
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    plan = asyncio.run(planner.request_turn_async(task="Write firmware", tool_results=(),
        allowed_tools=("write_file",), run_id="run", turn=3, stream=False))
    assert len(router.requests) == 2 and plan.tool_calls[0].call_id == "good"
    assert '"missing_required": ["content"]' in router.requests[1].system_prompt
    assert '"required": ["path", "content"]' in router.requests[1].system_prompt
    rejected = next(event for event in diagnostics if event["event"] == "provider.toolplan.rejected")
    assert rejected["tool"] == "write_file" and rejected["missing_required"] == ["content"]
    assert rejected["parse_reason"] == "product_tool_arguments_invalid"
    assert "private-name" not in json.dumps(diagnostics) and "private source" not in json.dumps(diagnostics)
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


@pytest.mark.parametrize("name,args,code", [
    ("write", {"path": "main.cpp"}, "TOOL_ARGUMENTS_INVALID"),
    ("write", {"path": "main.cpp", "content": "secret", "secret-unexpected-key": True}, "TOOL_ARGUMENTS_INVALID"),
    ("invented_tool", {}, "TOOLPLAN_TOOL_UNKNOWN"),
])
def test_invalid_native_calls_stay_bounded_and_do_not_execute(name, args, code):
    from backend.agent_runtime.api_planner_provider import ApiPlannerError
    router = Router(ModelResponse("", "openrouter", "native-model", tool_calls=(
        {"id": "bad", "name": name, "arguments": args},)))
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    with pytest.raises(ApiPlannerError) as caught:
        asyncio.run(planner.request_turn_async(task="Write firmware", tool_results=(),
            allowed_tools=("write_file",), run_id="run", turn=1, stream=False))
    assert caught.value.safe_code == code and len(router.requests) == 2
    assert planner.to_safe_metadata()["last_failure_origin"] == "tool_validation"
    assert "secret" not in json.dumps(caught.value.details)
    assert planner._native_messages == []


def test_router_planner_sends_real_tools_and_decodes_calls():
    router = Router(ModelResponse(
        "", "openrouter", "native-model",
        tool_calls=({"id": "call-1", "name": "read", "arguments": {"path": "src/main.cpp"}},),
    ))
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    result = asyncio.run(planner.request_turn_async(
        task="Inspect src/main.cpp", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=1, stream=False,
    ))
    assert result.final is False
    assert result.tool_calls[0].tool_type == "read_file"
    request = router.requests[0]
    assert request.structured_output is None
    tools = request.metadata["forgex_native_tools"]
    assert tools[0]["function"]["name"] == "read"


def test_native_text_without_calls_is_a_final_answer():
    router = Router(ModelResponse("Inspection complete.", "openrouter", "native-model"))
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    result = asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=2, stream=False,
    ))
    assert result.final and result.summary == "Inspection complete."


def test_native_turn_preserves_call_id_and_returns_real_tool_message():
    router = Router([
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "provider-call", "name": "read", "arguments": {"path": "src/main.cpp"}},
        )),
        ModelResponse("Done.", "openrouter", "native-model"),
    ])
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    first = asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=1, stream=False,
    ))
    second = asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=({
            "call_id": first.tool_calls[0].call_id, "tool": "read_file",
            "status": "completed", "output": {"path": "src/main.cpp", "content": "void setup() {}"},
        },), allowed_tools=("read_file",), run_id="run", turn=2, stream=False,
    ))
    assert second.final
    messages = router.requests[1].metadata["forgex_messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["id"] == "provider-call"
    assert messages[2]["tool_call_id"] == "provider-call"


def test_native_results_survive_a_shrinking_compacted_observation_view():
    router = Router([
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "first", "name": "read", "arguments": {"path": "first.cpp"}},)),
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "second", "name": "read", "arguments": {"path": "second.cpp"}},)),
        ModelResponse("Done.", "openrouter", "native-model"),
    ])
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=1, stream=False,
    ))
    first = {"call_id": "first", "tool": "read_file", "status": "completed",
             "output": {"content": "source one"}}
    asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=({"call_id": "checkpoint-1", "tool": "context_compaction",
                                       "status": "completed"}, first),
        allowed_tools=("read_file",), run_id="run", turn=2, stream=False,
    ))
    second = {"call_id": "second", "tool": "read_file", "status": "completed",
              "output": {"content": "source two"}}
    asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=({"call_id": "checkpoint-2", "tool": "context_compaction",
                                       "status": "completed"}, second),
        allowed_tools=("read_file",), run_id="run", turn=3, stream=False,
    ))
    messages = router.requests[-1].metadata["forgex_messages"]
    delivered = [json.loads(message["content"]) for message in messages
                 if message["role"] == "tool"]
    assert [item["call_id"] for item in delivered] == ["first", "second"]
    assert all(item["status"] == "completed" for item in delivered)


@pytest.mark.parametrize("keep_settled_observation", [True, False])
def test_native_continuation_after_final_answer_keeps_completed_tool_exchange(keep_settled_observation):
    router = Router([
        ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
            {"id": "read-source", "name": "read", "arguments": {"path": "src/main.cpp"}},)),
        ModelResponse("Firmware built successfully.", "nvidia_nim", "native-model"),
        ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
            {"id": "read-requirements", "name": "read", "arguments": {"path": "README.md"}},)),
        ModelResponse("Verification complete.", "nvidia_nim", "native-model"),
    ])
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="native-model")
    source = {"call_id": "read-source", "tool": "read_file", "status": "completed",
              "output": {"content": "void setup() {}"}}
    verification = {"call_id": "requirements_2", "tool": "requirement_validation",
                    "status": "completed", "output": {"message": "Verify the source evidence before finishing."}}

    async def turn(number, observations):
        return await planner.request_turn_async(
            task="Build and verify firmware", tool_results=observations,
            allowed_tools=("read_file",), run_id="run", turn=number, stream=False,
        )

    assert asyncio.run(turn(1, ())).tool_calls[0].call_id == "read-source"
    assert asyncio.run(turn(2, (source,))).final
    observations = (source, verification) if keep_settled_observation else (
        {"call_id": "checkpoint-1", "tool": "context_compaction", "status": "completed"},
        verification,
    )
    continued = asyncio.run(turn(3, observations))
    assert continued.tool_calls[0].call_id == "read-requirements"
    messages = router.requests[2].metadata["forgex_messages"]
    delivered = [message for message in messages if message["role"] == "tool"]
    assert len(delivered) == 1 and delivered[0]["tool_call_id"] == "read-source"
    assert json.loads(delivered[0]["content"]) == source
    assert "Verify the source evidence before finishing." in messages[-1]["content"]
    assert asyncio.run(turn(4, ({"call_id": "read-requirements", "tool": "read_file",
                               "status": "completed", "output": {"content": "Requirements"}},))).final
    assert len(router.requests) == 4


@pytest.mark.parametrize("settled_call_ids", [(), ("read-source",)])
def test_native_missing_tool_result_still_stops_before_provider_request(settled_call_ids):
    from backend.agent_runtime.api_planner_provider import ApiPlannerError

    router = Router(ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
        {"id": "read-source", "name": "read", "arguments": {"path": "src/main.cpp"}},
        {"id": "read-requirements", "name": "read", "arguments": {"path": "README.md"}},
    )))
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="native-model")
    asyncio.run(planner.request_turn_async(
        task="Inspect", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=1, stream=False,
    ))
    committed = json.dumps(planner._native_messages)
    with pytest.raises(ApiPlannerError) as caught:
        asyncio.run(planner.request_turn_async(
            task="Inspect", tool_results=tuple(
                {"call_id": call_id, "tool": "read_file", "status": "completed"}
                for call_id in settled_call_ids
            ), allowed_tools=("read_file",), run_id="run", turn=2, stream=False,
        ))
    assert caught.value.safe_code == "AGENT_TOOL_RESULT_MISSING"
    assert caught.value.details["call_id"] == ("read-requirements" if settled_call_ids else "read-source")
    assert len(router.requests) == 1
    assert json.dumps(planner._native_messages) == committed


def test_autonomous_native_loop_finishes_after_host_build_and_verification(tmp_path, monkeypatch):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from tests.unit.test_agent_verification_freshness import Builder

    (tmp_path / "main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
    router = Router([
        ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
            {"id": "read-source", "name": "read", "arguments": {"path": "main.cpp"}},)),
        ModelResponse("Source is ready to build.", "nvidia_nim", "native-model"),
        ModelResponse("Build passed; verify the firmware.", "nvidia_nim", "native-model"),
        ModelResponse("Build and verification passed.", "nvidia_nim", "native-model"),
    ])
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="native-model")
    builder = Builder()
    events = []

    async def progress(event, payload):
        events.append((event, payload))

    loop = AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=builder, progress=progress,
        policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True,
    )

    async def simulate(_timeout, *, call_id):
        assert loop.build_passed and loop._refresh_verification()
        loop.simulation_attempted = loop.simulation_passed = True
        loop.last_simulation = {"success": True, "message": "Host verification passed."}
        return {"call_id": call_id, "tool": "run_simulation", "status": "completed",
                "output": loop.last_simulation}

    monkeypatch.setattr(loop, "_simulate", simulate)
    result = asyncio.run(loop.run(
        task="Inspect, build and verify firmware", require_build=True, require_simulation=True,
    ))
    assert result.success, (result.classification, result.message)
    assert result.classification == "AGENT_V3_COMPLETED"
    assert result.build_verified and loop.simulation_passed and builder.calls == 1
    assert len(router.requests) == 4
    final_messages = router.requests[-1].metadata["forgex_messages"]
    assert sum(message.get("tool_call_id") == "read-source" for message in final_messages) == 1
    assert "Host verification passed." in json.dumps(final_messages)
    assert not any(event == "MODEL_ATTEMPT_SETTLED" and payload.get("status") == "failed"
                   for event, payload in events)


def test_native_latest_task_revision_reaches_provider_messages():
    router = Router([
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": "read-1", "name": "read", "arguments": {"path": "src/main.cpp"}},)),
        ModelResponse("Done.", "openrouter", "native-model"),
    ])
    planner = RouterProductPlanner(router, provider_id="openrouter", model_id="native-model")
    asyncio.run(planner.request_turn_async(
        task="Use pin 4", tool_results=(), allowed_tools=("read_file",),
        run_id="run", turn=1, stream=False,
    ))
    asyncio.run(planner.request_turn_async(
        task="Use pin 5", tool_results=({"call_id": "read-1", "tool": "read_file",
                                        "status": "completed", "output": {"content": "source"}},),
        allowed_tools=("read_file",), run_id="run", turn=2, stream=False,
    ))
    messages = router.requests[-1].metadata["forgex_messages"]
    assert "Use pin 5" in json.dumps(messages)
    assert messages[-1]["role"] == "user"
    assert "Return exactly one JSON object" not in router.requests[-1].system_prompt


def test_native_history_compacts_only_complete_tool_exchanges():
    responses = [
        ModelResponse("", "openrouter", "native-model", tool_calls=(
            {"id": f"call-{index}", "name": "read", "arguments": {"path": f"file-{index}.txt"}},
        ))
        for index in range(6)
    ]
    router = Router(responses, context_window=4096)
    planner = RouterProductPlanner(
        router, provider_id="openrouter", model_id="native-model", max_tokens=1024
    )
    observations = []
    for turn in range(1, 7):
        plan = asyncio.run(planner.request_turn_async(
            task="Inspect the project", tool_results=tuple(observations),
            allowed_tools=("read_file",), run_id="run", turn=turn, stream=False,
        ))
        observations.append({
            "call_id": plan.tool_calls[0].call_id,
            "tool": "read_file",
            "status": "completed",
            "output": "x" * 5000,
        })

    messages = router.requests[-1].metadata["forgex_messages"]
    assert messages[0]["role"] == "user"
    assert any("ForgeX context checkpoint" in str(item.get("content")) for item in messages)
    call_ids = {
        call["id"]
        for message in messages
        for call in message.get("tool_calls", ())
    }
    result_ids = {
        message["tool_call_id"] for message in messages if message["role"] == "tool"
    }
    assert call_ids == result_ids
    metadata = planner.to_safe_metadata()
    assert metadata["native_context_compactions"] >= 1
    assert metadata["native_context_omitted_messages"] >= 1


def _request():
    return LLMRequest(prompt="Inspect", metadata={"forgex_native_tools": [TOOL]})


def _conversation_request():
    return LLMRequest(prompt="unused", metadata={
        "forgex_native_tools": [TOOL],
        "forgex_messages": [
            {"role": "user", "content": "Inspect"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "name": "read", "arguments": {"path": "main.cpp"}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "name": "read", "content": "source"},
        ],
    })


def test_openai_compatible_native_tool_payload():
    payload = OpenAIService(api_key="key", model="model")._payload(_request(), stream=False)
    assert payload["tools"][0]["function"]["name"] == "read"
    assert payload["tool_choice"] == "auto"
    json.dumps(payload)


def test_openai_responses_native_tool_payload():
    payload = OpenAIResponsesService(api_key="key", model="model")._payload(_request(), stream=False)
    assert payload["tools"][0]["name"] == "read"
    assert payload["tools"][0]["parameters"]["required"] == ["path"]
    json.dumps(payload)


def test_anthropic_native_tool_payload():
    payload = AnthropicService(api_key="key", model="model")._payload(_request(), stream=False)
    assert payload["tools"][0]["name"] == "read"
    assert payload["tools"][0]["input_schema"]["required"] == ["path"]
    json.dumps(payload)


def test_gemini_native_tool_payload():
    payload = GeminiService(api_key="key", model="model")._payload(_request(), stream=False)
    declaration = payload["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "read"
    assert payload["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    json.dumps(payload)


def test_protocols_serialize_native_conversation_roles():
    openai = OpenAIService(api_key="key", model="model")._payload(_conversation_request(), stream=False)
    assert openai["messages"][1]["tool_calls"][0]["id"] == "call-1"
    assert openai["messages"][2]["tool_call_id"] == "call-1"
    anthropic = AnthropicService(api_key="key", model="model")._payload(_conversation_request(), stream=False)
    assert anthropic["messages"][1]["content"][0]["type"] == "tool_use"
    assert anthropic["messages"][2]["content"][0]["tool_use_id"] == "call-1"
    gemini = GeminiService(api_key="key", model="model")._payload(_conversation_request(), stream=False)
    assert gemini["contents"][1]["parts"][0]["functionCall"]["name"] == "read"
    assert gemini["contents"][2]["parts"][0]["functionResponse"]["name"] == "read"
    json.dumps(openai); json.dumps(anthropic); json.dumps(gemini)
