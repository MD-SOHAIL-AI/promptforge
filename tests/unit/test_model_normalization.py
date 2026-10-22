from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.agent_runtime.api_planner_provider import (
    API_PLANNER_CONFIGS,
    ApiPlannerClassification,
    ApiPlannerError,
    ApiPlannerProvider,
    ApiTransportHTTPError,
)
from backend.agent_runtime.run_reporting import (
    ProviderErrorCode,
    actionable_message,
    normalize_provider_error,
)
from backend.model_router.models import ModelCapabilities
from backend.model_router.normalization import (
    CanonicalStreamAssembler,
    ProviderNormalizationError,
    normalize_response,
    normalize_stream_event,
)
from backend.services.llm_service import LLMRequest, OpenRouterService


TOOL_PLAN = {
    "version": "forgex.toolplan.v2",
    "summary": "Inspect source",
    "tool_calls": [
        {"id": "call_1", "type": "read_file", "arguments": {"path": "src/main.cpp"}}
    ],
    "final": False,
}


@pytest.mark.parametrize(
    ("provider_id", "raw", "expected"),
    [
        ("openai", {"choices": [{"message": {"content": "answer"}}]}, "answer"),
        (
            "openrouter",
            {"choices": [{"message": {"content": [{"type": "text", "text": "array answer"}]}}]},
            "array answer",
        ),
        ("openai", {"output_text": "responses answer"}, "responses answer"),
        (
            "gemini",
            {"candidates": [{"content": {"parts": [{"text": "native answer"}]}}]},
            "native answer",
        ),
        ("groq", {"choices": [{"message": {"content": "groq answer"}}]}, "groq answer"),
        ("lmstudio", {"choices": [{"message": {"content": "local answer"}}]}, "local answer"),
        ("ollama", {"message": {"content": "ollama answer"}, "done": True}, "ollama answer"),
    ],
)
def test_provider_envelopes_normalize_to_one_contract(provider_id, raw, expected) -> None:
    result = normalize_response(raw, provider_id=provider_id, model_id="model")
    assert result.text == expected


def test_reasoning_is_separate_from_final_answer() -> None:
    result = normalize_response(
        {
            "choices": [
                {"message": {"reasoning_content": "private reasoning", "content": "final answer"}}
            ]
        },
        provider_id="openrouter",
        model_id="reasoning-model",
    )
    assert result.reasoning == "private reasoning"
    assert result.text == "final answer"


def test_structured_json_and_native_tools_are_canonical() -> None:
    structured = normalize_response(
        {"choices": [{"message": {"content": json.dumps(TOOL_PLAN)}}]},
        provider_id="openai",
        model_id="gpt-4.1",
    )
    assert structured.structured_output == TOOL_PLAN
    native = normalize_response(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "tool-1",
                                "type": "function",
                                "function": {
                                    "name": "submit_tool_plan",
                                    "arguments": json.dumps(TOOL_PLAN),
                                },
                            }
                        ],
                    }
                }
            ]
        },
        provider_id="openai",
        model_id="gpt-4.1",
    )
    assert native.text is None
    assert native.tool_calls[0]["arguments"] == TOOL_PLAN


def test_empty_truncated_and_malformed_outputs_have_distinct_codes() -> None:
    with pytest.raises(ProviderNormalizationError) as empty:
        normalize_response({"choices": []}, provider_id="openai", model_id="m")
    assert empty.value.code == "MODEL_OUTPUT_EMPTY"
    with pytest.raises(ProviderNormalizationError) as truncated:
        normalize_response(
            {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]},
            provider_id="openai",
            model_id="m",
        )
    assert truncated.value.code == "MODEL_OUTPUT_TRUNCATED"
    with pytest.raises(ProviderNormalizationError) as malformed:
        normalize_response({"choices": ["bad"]}, provider_id="openai", model_id="m")
    assert malformed.value.code == "PROVIDER_ENVELOPE_INVALID"


def test_stream_packets_normalize_without_promoting_reasoning() -> None:
    reasoning = normalize_stream_event(
        {"choices": [{"delta": {"reasoning_content": "think"}}]},
        provider_id="openrouter",
    )
    text = normalize_stream_event(
        {"choices": [{"delta": {"content": [{"type": "text", "text": "answer"}]}}]},
        provider_id="openrouter",
    )
    done = normalize_stream_event(
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        provider_id="openrouter",
    )
    assert [(event.type, event.text) for event in reasoning] == [("reasoning_delta", "think")]
    assert [(event.type, event.text) for event in text] == [("text_delta", "answer")]
    assert done[0].type == "done"


def test_gemini_reasoning_packet_does_not_require_final_text() -> None:
    events = normalize_stream_event(
        {
            "candidates": [
                {"content": {"parts": [{"thought": True, "text": "working"}]}}
            ]
        },
        provider_id="gemini",
    )
    assert [(event.type, event.text) for event in events] == [
        ("reasoning_delta", "working")
    ]


def test_anthropic_content_blocks_share_the_canonical_contract() -> None:
    response = normalize_response(
        {
            "id": "msg_1",
            "model": "claude-model",
            "content": [
                {"type": "thinking", "thinking": "private reasoning"},
                {"type": "text", "text": "final answer"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 4, "output_tokens": 2},
        },
        provider_id="anthropic",
        model_id="claude-model",
    )
    assert response.text == "final answer"
    assert response.reasoning == "private reasoning"
    assert response.usage == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}


@pytest.mark.parametrize(
    ("capabilities", "expected_key", "expected_mode"),
    [
        (ModelCapabilities(json_schema=True, native_tools=True), "response_format", "json_schema"),
        (ModelCapabilities(native_tools=True), "tools", "native_tools"),
        (ModelCapabilities(), None, "prompted_json"),
    ],
)
def test_agent_uses_best_supported_structured_output(capabilities, expected_key, expected_mode) -> None:
    payloads: list[dict[str, object]] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        payloads.append(dict(payload))
        if expected_mode == "native_tools":
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "submit_tool_plan",
                                        "arguments": json.dumps(TOOL_PLAN),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"content": json.dumps(TOOL_PLAN)}}]}

    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="model",
        transport=transport,
        capabilities_by_model={"model": capabilities},
    )
    plan = planner.request_turn(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("read_file",),
        run_id="run",
        turn=1,
    )
    assert plan.tool_calls[0].tool_type == "read_file"
    assert planner.to_safe_metadata()["structured_output_mode"] == expected_mode
    if expected_key is None:
        assert "response_format" not in payloads[0] and "tools" not in payloads[0]
    else:
        assert expected_key in payloads[0]


def test_http_200_toolplan_conversion_failure_is_not_attributed_to_provider() -> None:
    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: {"choices": [{"message": {"content": "not json"}}]},
    )
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Inspect firmware",
            tool_results=(),
            allowed_tools=("read_file",),
            run_id="run",
            turn=1,
        )
    assert caught.value.classification == ApiPlannerClassification.OUTPUT_NOT_JSON.value
    assert normalize_provider_error(caught.value.safe_code) == ProviderErrorCode.MODEL_OUTPUT_NOT_JSON
    assert "ForgeX could not convert" in actionable_message(
        ProviderErrorCode.MODEL_OUTPUT_NOT_JSON, "OpenAI"
    )


def test_valid_json_with_invalid_toolplan_has_schema_error() -> None:
    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: {"choices": [{"message": {"content": '{"unexpected":true}'}}]},
    )
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Inspect firmware",
            tool_results=(),
            allowed_tools=("read_file",),
            run_id="run",
            turn=1,
        )
    assert caught.value.safe_code == "TOOLPLAN_SCHEMA_INVALID"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "PROVIDER_AUTH_ERROR"),
        (429, "PROVIDER_RATE_LIMITED"),
        (503, "PROVIDER_UNAVAILABLE"),
    ],
)
def test_agent_transport_errors_keep_the_failing_stage(status, expected) -> None:
    def transport(*args):
        raise ApiTransportHTTPError(status, "upstream failure")

    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="model",
        transport=transport,
    )
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Inspect firmware",
            tool_results=(),
            allowed_tools=("read_file",),
            run_id="run",
            turn=1,
        )
    assert caught.value.safe_code == expected


def test_same_envelope_succeeds_for_chat_normalization_and_agent_planning() -> None:
    envelope = {"choices": [{"message": {"content": json.dumps(TOOL_PLAN)}}]}

    async def chat_request():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=envelope, request=request)
            )
        )
        service = OpenRouterService(api_key="secret", model="model", client=client)
        try:
            return await service.generate(LLMRequest(prompt="Inspect firmware"))
        finally:
            await client.aclose()

    chat = asyncio.run(chat_request())
    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: envelope,
    )
    agent = planner.request_turn(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("read_file",),
        run_id="run",
        turn=1,
    )
    assert chat.structured_output == TOOL_PLAN
    assert agent.summary == TOOL_PLAN["summary"]


def test_gemini_reasoning_only_stream_packet_is_not_final_text() -> None:
    events = normalize_stream_event(
        {
            "candidates": [
                {"content": {"parts": [{"thought": True, "text": "working"}]}}
            ]
        },
        provider_id="gemini",
    )
    assert [(event.type, event.text) for event in events] == [
        ("reasoning_delta", "working")
    ]


def test_transport_done_sentinel_is_provider_neutral() -> None:
    for provider_id in ("openai", "openrouter", "gemini", "ollama", "anthropic"):
        events = normalize_stream_event(
            {"type": "forgex.stream.done"}, provider_id=provider_id
        )
        assert events[0].type == "done"


def test_gemini_native_function_call_stream_is_canonical() -> None:
    events = normalize_stream_event(
        {
            "candidates": [{
                "content": {"parts": [{
                    "functionCall": {
                        "id": "call-1",
                        "name": "submit_tool_plan",
                        "args": TOOL_PLAN,
                    }
                }]},
                "finishReason": "STOP",
            }]
        },
        provider_id="gemini",
    )
    assert events[0].type == "tool_call_delta"
    assert events[0].tool_call == {
        "id": "call-1",
        "name": "submit_tool_plan",
        "arguments": TOOL_PLAN,
    }
    assert events[1].type == "done"


def test_anthropic_native_tool_stream_assembles_partial_json() -> None:
    assembler = CanonicalStreamAssembler()
    packets = (
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "call-1",
                "name": "submit_tool_plan",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(TOOL_PLAN),
            },
        },
        {"type": "message_stop"},
    )
    for packet in packets:
        for event in normalize_stream_event(packet, provider_id="anthropic"):
            assembler.add(event)
    response = assembler.response(provider_id="anthropic", model_id="claude")
    assert response.tool_calls[0]["arguments"] == TOOL_PLAN


def test_openai_responses_tool_stream_assembles_arguments() -> None:
    assembler = CanonicalStreamAssembler()
    packets = (
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call-1",
                "name": "submit_tool_plan",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "item_id": "call-1",
            "delta": json.dumps(TOOL_PLAN),
        },
        {"type": "response.completed"},
    )
    for packet in packets:
        for event in normalize_stream_event(packet, provider_id="openai"):
            assembler.add(event)
    response = assembler.response(provider_id="openai", model_id="gpt")
    assert response.tool_calls[0]["arguments"] == TOOL_PLAN


def test_agent_rejects_truncated_content_before_toolplan_validation() -> None:
    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: {
            "choices": [
                {
                    "message": {"content": json.dumps(TOOL_PLAN)},
                    "finish_reason": "length",
                }
            ]
        },
    )
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Inspect firmware",
            tool_results=(),
            allowed_tools=("read_file",),
            run_id="run",
            turn=1,
        )
    assert caught.value.safe_code == "MODEL_OUTPUT_TRUNCATED"
