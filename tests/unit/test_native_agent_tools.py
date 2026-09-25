import asyncio
import json

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
