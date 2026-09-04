from __future__ import annotations

import json

import pytest

from backend.agent_runtime.api_planner_provider import (
    API_PLANNER_CONFIGS,
    ApiPlannerClassification,
    ApiPlannerError,
    ApiPlannerProvider,
    ApiTransportHTTPError,
)


def envelope(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def tool_envelope(arguments: str) -> dict[str, object]:
    return {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "type": "function",
                    "function": {"name": "submit_toolplan", "arguments": arguments},
                }],
            },
        }],
    }


def valid_plan(path: str = "src/main.cpp", content: str = "int main() { return 0; }\n") -> str:
    return json.dumps({
        "version": "forgex.toolplan.v1",
        "summary": "write source",
        "tool_calls": [{"id": "call_1", "type": "write_file", "path": path, "content": content}],
        "final": False,
    })


def test_provider_configs_share_only_transport_metadata() -> None:
    assert set(API_PLANNER_CONFIGS) == {"openrouter", "openai", "groq", "gemini"}
    assert all(config.endpoint.startswith("https://") for config in API_PLANNER_CONFIGS.values())
    assert not hasattr(next(iter(API_PLANNER_CONFIGS.values())), "api_key_env")
    assert not hasattr(next(iter(API_PLANNER_CONFIGS.values())), "provider_flag")


def test_api_planner_uses_explicit_model_and_key_without_env_registry() -> None:
    calls: list[tuple[str, str, str]] = []

    def transport(url, payload, api_key, headers, timeout):
        del headers, timeout
        calls.append((url, str(payload["model"]), api_key))
        return envelope(valid_plan())

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="secret", model_id="gpt-test", transport=transport)
    plan = provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert plan.tool_calls[0].path == "src/main.cpp"
    assert calls == [(API_PLANNER_CONFIGS["openai"].endpoint, "gpt-test", "secret")]
    assert provider.to_safe_metadata()["classification"] == ApiPlannerClassification.READY.value
    assert "secret" not in json.dumps(provider.to_safe_metadata())


def test_api_planner_accepts_expanded_v3_tool_surface() -> None:
    called = False

    def transport(*args):
        nonlocal called
        called = True
        return envelope(valid_plan())

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="secret", model_id="gpt-test", transport=transport)
    plan = provider.request_turn(
        task="Create firmware",
        tool_results=(),
        allowed_tools=("list_files", "grep_search", "read_file", "write_file", "build_firmware"),
        run_id="run",
        turn=1,
    )
    assert plan.tool_calls[0].tool_type == "write_file"
    assert called is True


def test_api_planner_rejects_unknown_tool_surface_before_network() -> None:
    called = False

    def transport(*args):
        nonlocal called
        called = True
        return envelope(valid_plan())

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="secret", model_id="gpt-test", transport=transport)
    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file", "shell"), run_id="run", turn=1)
    assert caught.value.classification == ApiPlannerClassification.TOOLPLAN_UNSAFE.value
    assert called is False


def test_api_planner_rejects_unsafe_model_path() -> None:
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(valid_plan("../outside.txt", "x")),
    )
    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert caught.value.classification == ApiPlannerClassification.TOOLPLAN_UNSAFE.value


def test_api_planner_maps_rate_limit_without_leaking_body() -> None:
    def transport(*args):
        raise ApiTransportHTTPError(429, "rate limit from provider secret detail", "req-123")

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openrouter"], api_key="secret", model_id="model", transport=transport)
    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert caught.value.classification == ApiPlannerClassification.RATE_LIMITED.value
    metadata = provider.to_safe_metadata()
    assert metadata["http_status"] == 429
    assert metadata["provider_request_id"] == "req-123"
    assert "secret detail" not in json.dumps(metadata)


def test_second_turn_requires_completed_tool_result() -> None:
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"], api_key="secret", model_id="model", transport=lambda *args: envelope(valid_plan()),
    )
    provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    final = provider.request_turn(task="Create firmware", tool_results=({"status": "completed"},), allowed_tools=("write_file",), run_id="run", turn=2)
    assert final.final is True
    assert final.tool_calls == ()


def test_api_planner_normalizes_common_tool_call_shape_without_weakening_paths() -> None:
    content = json.dumps({
        "summary": "write source",
        "tool_calls": [{
            "tool": "write_file",
            "arguments": {"path": "src/main.cpp", "content": "void setup() {}\nvoid loop() {}\n"},
        }],
        "final": False,
    })
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: envelope(content),
    )

    plan = provider.request_turn(
        task="Create firmware",
        tool_results=(),
        allowed_tools=("write_file",),
        run_id="run",
        turn=1,
    )

    assert plan.tool_calls[0].call_id == "call_1"
    assert plan.tool_calls[0].path == "src/main.cpp"
    assert provider.to_safe_metadata()["toolplan_normalized"] is True
    assert provider.outbound_request_count == 1


def test_api_planner_repairs_an_invalid_schema_once() -> None:
    responses = iter((envelope('{"message":"not a tool plan"}'), envelope(valid_plan())))
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: next(responses),
    )

    plan = provider.request_turn(
        task="Create firmware",
        tool_results=(),
        allowed_tools=("write_file",),
        run_id="run",
        turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert provider.outbound_request_count == 2
    assert provider.to_safe_metadata()["toolplan_repair_attempted"] is True


def test_api_planner_uses_native_tool_submission_when_model_supports_tools() -> None:
    payloads: list[dict[str, object]] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        payloads.append(dict(payload))
        return tool_envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="tool-model",
        transport=transport,
        supported_parameters=("tools", "tool_choice"),
    )

    plan = provider.request_turn(
        task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert "response_format" not in payloads[0]
    assert payloads[0]["tools"][0]["function"]["name"] == "submit_toolplan"  # type: ignore[index]
    assert payloads[0]["provider"] == {"require_parameters": True}
    assert provider.to_safe_metadata()["toolplan_mode"] == "native_tool"


def test_api_planner_prefers_json_mode_when_model_supports_both_protocols() -> None:
    payloads: list[dict[str, object]] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        payloads.append(dict(payload))
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="tool-and-json-model",
        transport=transport,
        supported_parameters=("response_format", "tools", "tool_choice"),
    )

    plan = provider.request_turn(
        task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in payloads[0]
    assert "tool_choice" not in payloads[0]
    assert provider.to_safe_metadata()["toolplan_mode"] == "json_object"


def test_api_planner_accepts_content_json_when_native_tool_model_ignores_tool_call() -> None:
    payloads: list[dict[str, object]] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        payloads.append(dict(payload))
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="tool-only-model",
        transport=transport,
        supported_parameters=("tools", "tool_choice"),
    )

    plan = provider.request_turn(
        task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert "response_format" not in payloads[0]
    assert payloads[0]["tools"][0]["function"]["name"] == "submit_toolplan"  # type: ignore[index]
    assert provider.to_safe_metadata()["toolplan_mode"] == "native_tool"


def test_api_planner_rejects_known_incompatible_model_before_network() -> None:
    called = False

    def transport(*args):
        nonlocal called
        called = True
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="chat-only",
        transport=transport,
        supported_parameters=("temperature", "max_tokens"),
    )

    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(
            task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
        )

    assert caught.value.safe_code == "provider_model_toolplan_unsupported"
    assert called is False


def test_api_planner_falls_back_when_saved_model_is_known_incompatible() -> None:
    models: list[str] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        models.append(str(payload["model"]))
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="chat-only",
        supported_parameters=("temperature",),
        fallback_model_id="openrouter/free",
        fallback_supported_parameters=("response_format",),
        transport=transport,
    )

    provider.request_turn(
        task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
    )

    assert models == ["openrouter/free"]
    assert provider.to_safe_metadata()["fallback_reason"] == ApiPlannerClassification.MODEL_UNAVAILABLE.value


def test_api_planner_falls_back_to_configured_model_after_invalid_toolplans() -> None:
    payloads: list[dict[str, object]] = []
    responses = iter((
        envelope('{"message":"invalid"}'),
        envelope('{"message":"still invalid"}'),
        envelope(valid_plan()),
    ))

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        payloads.append(dict(payload))
        return next(responses)

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="primary-model",
        fallback_model_id="openrouter/free",
        transport=transport,
    )

    plan = provider.request_turn(
        task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert [payload["model"] for payload in payloads] == ["primary-model", "primary-model", "openrouter/free"]
    metadata = provider.to_safe_metadata()
    assert metadata["fallback_used"] is True
    assert metadata["fallback_reason"] == ApiPlannerClassification.TOOLPLAN_INVALID.value
    assert metadata["model_id"] == "openrouter/free"
