from __future__ import annotations

import json

import pytest

from backend.agent_runtime.api_planner_provider import (
    API_PLANNER_CONFIGS,
    ApiPlannerClassification,
    ApiPlannerError,
    ApiPlannerProvider,
    ApiTransportHTTPError,
    _planner_instruction,
)


def envelope(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


def valid_plan(path: str = "src/main.cpp", content: str = "int main() { return 0; }\n") -> str:
    return json.dumps({
        "version": "forgex.toolplan.v1",
        "summary": "write source",
        "tool_calls": [{"id": "call_1", "type": "write_file", "path": path, "content": content}],
        "final": False,
    })


def tool_plan(tool: str, arguments: dict[str, object], *, final: bool = False) -> str:
    return json.dumps({
        "version": "forgex.toolplan.v2",
        "summary": "next action",
        "tool_calls": [] if final else [{"id": "call_1", "type": tool, "arguments": arguments}],
        "final": final,
    })


def test_planner_instruction_explains_nested_file_creation() -> None:
    instruction = _planner_instruction(allowed_tools=("write_file",))
    assert "write_file creates missing parent directories automatically" in instruction
    assert "Never call write_file for a directory alone" in instruction
    assert "project-relative path" in instruction


def test_provider_configs_share_only_transport_metadata() -> None:
    assert set(API_PLANNER_CONFIGS) == {
        "openrouter",
        "openai",
        "groq",
        "nvidia_nim",
        "gemini",
    }
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


def test_api_planner_logs_provider_request_start_and_completion(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "provider.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))

    def transport(url, payload, api_key, headers, timeout):
        del url, payload, api_key, headers, timeout
        return {
            "id": "provider-req-1",
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            "choices": [{"message": {"content": valid_plan()}, "finish_reason": "stop"}],
        }

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openrouter"], api_key="sk-or-v1-secretsecretsecret", model_id="model-x", transport=transport)
    provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run-123", turn=1)

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["provider.request.started", "provider.request.completed"]
    assert events[0]["provider"] == "openrouter"
    assert events[0]["model"] == "model-x"
    assert events[0]["run_id"] == "run-123"
    assert events[0]["turn_id"] == "run-123:turn-1"
    assert events[1]["status"] == 200
    assert events[1]["input_tokens"] == 12
    assert events[1]["output_tokens"] == 7
    assert events[1]["finish_reason"] == "stop"
    assert "sk-or-v1-secretsecretsecret" not in log_path.read_text(encoding="utf-8")


def test_api_planner_logs_provider_failure(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "provider.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))

    def transport(*args):
        raise ApiTransportHTTPError(401, "bad Bearer sk-or-v1-secretsecretsecret", "req-provider")

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openrouter"], api_key="sk-or-v1-secretsecretsecret", model_id="model-x", transport=transport)
    with pytest.raises(ApiPlannerError):
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run-123", turn=1)

    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["provider.request.started", "provider.request.failed"]
    assert events[1]["status"] == 401
    assert events[1]["error_code"] == "http_status_401"
    assert events[1]["provider_request_id"] == "req-provider"
    assert "sk-or-v1-secretsecretsecret" not in log_path.read_text(encoding="utf-8")


def test_openrouter_free_model_failure_retries_agent_planner_fallback(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "provider.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    models: list[str] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        model = str(payload["model"])
        models.append(model)
        if model == "openai/gpt-oss-20b:free":
            raise ApiTransportHTTPError(
                404,
                json.dumps({"error": {"message": "This model is unavailable for free. The paid version is available now.", "code": 404}}),
                "req-unavailable",
            )
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="sk-or-v1-secretsecretsecret",
        model_id="openai/gpt-oss-20b:free",
        transport=transport,
        fallback_model_ids=("cohere/north-mini-code:free",),
    )

    plan = provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run-123", turn=1)

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert models == ["openai/gpt-oss-20b:free", "cohere/north-mini-code:free"]
    metadata = provider.to_safe_metadata()
    assert metadata["classification"] == ApiPlannerClassification.READY.value
    assert metadata["model_id"] == "cohere/north-mini-code:free"
    assert metadata["fallback_used"] is True
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "provider.request.started",
        "provider.request.failed",
        "provider.request.started",
        "provider.request.completed",
    ]
    assert events[1]["model"] == "openai/gpt-oss-20b:free"
    assert events[1]["status"] == 404
    assert events[2]["model"] == "cohere/north-mini-code:free"
    assert events[3]["model"] == "cohere/north-mini-code:free"
    assert "sk-or-v1-secretsecretsecret" not in log_path.read_text(encoding="utf-8")


def test_openrouter_free_model_schema_failure_retries_agent_planner_fallback() -> None:
    models: list[str] = []

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        model = str(payload["model"])
        models.append(model)
        if model == "minimax/minimax-m3:free":
            return envelope("I will create the project, but here is prose instead of a tool plan.")
        return envelope(valid_plan())

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="sk-or-v1-secretsecretsecret",
        model_id="minimax/minimax-m3:free",
        transport=transport,
        fallback_model_ids=("cohere/north-mini-code:free",),
    )

    plan = provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run-123", turn=1)

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert models == ["minimax/minimax-m3:free", "minimax/minimax-m3:free", "cohere/north-mini-code:free"]
    metadata = provider.to_safe_metadata()
    assert metadata["classification"] == ApiPlannerClassification.READY.value
    assert metadata["model_id"] == "cohere/north-mini-code:free"
    assert metadata["fallback_used"] is True


def test_generation_task_allows_third_inspection_turn_without_model_fallback() -> None:
    models: list[str] = []
    observations = (
        {"status": "completed", "tool": "list_files", "output": {"files": []}},
        {"status": "completed", "tool": "update_plan", "output": {"steps": [{"step": "Inspect workspace", "status": "completed"}]}},
    )

    def transport(url, payload, api_key, headers, timeout):
        del url, api_key, headers, timeout
        model = str(payload["model"])
        models.append(model)
        return envelope(tool_plan("read_file", {"path": "platformio.ini"}))

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="sk-or-v1-secretsecretsecret",
        model_id="minimax/minimax-m3:free",
        transport=transport,
        fallback_model_ids=("cohere/north-mini-code:free",),
    )

    plan = provider.request_turn(
        task="USER TASK:\nCreate PlatformIO firmware\n\nWORKSPACE SUMMARY:\nFiles: 0",
        tool_results=observations,
        allowed_tools=("list_files", "read_file", "update_plan", "write_file", "build_firmware"),
        run_id="run-123",
        turn=3,
    )

    assert plan.tool_calls[0].tool_type == "read_file"
    assert models == ["minimax/minimax-m3:free"]
    metadata = provider.to_safe_metadata()
    assert metadata["model_id"] == "minimax/minimax-m3:free"
    assert metadata["fallback_used"] is False


def test_api_planner_allows_no_progress_for_explicit_plan_only_task() -> None:
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: envelope(tool_plan("list_files", {"path": "."})),
    )

    plan = provider.request_turn(
        task="Inspect the architecture and propose only; do not modify files.",
        tool_results=({"status": "completed", "tool": "list_files", "output": {"files": []}},),
        allowed_tools=("list_files", "update_plan", "write_file"),
        run_id="run-123",
        turn=3,
    )

    assert plan.tool_calls[0].tool_type == "list_files"
    assert provider.to_safe_metadata()["classification"] == ApiPlannerClassification.READY.value


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
    assert caught.value.safe_code == "provider_path_unsafe"


def test_api_planner_allows_inspection_root_path_before_runtime_policy() -> None:
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(tool_plan("list_files", {"path": "."})),
    )

    plan = provider.request_turn(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("list_files", "read_file"),
        run_id="run",
        turn=1,
    )

    assert plan.tool_calls[0].tool_type == "list_files"
    assert plan.tool_calls[0].arguments["path"] == "."
    assert provider.to_safe_metadata()["classification"] == ApiPlannerClassification.READY.value


def test_api_planner_normalizes_empty_inspection_paths() -> None:
    content = json.dumps({
        "version": "forgex.toolplan.v2",
        "summary": "inspect workspace",
        "tool_calls": [
            {"id": "call_1", "type": "list_files", "arguments": {}},
            {"id": "call_2", "type": "list_files", "arguments": {"path": None}},
            {"id": "call_3", "type": "list_files", "arguments": {"path": "./"}},
        ],
        "final": False,
    })
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(content),
    )

    plan = provider.request_turn(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("list_files",),
        run_id="run",
        turn=1,
    )

    assert [call.arguments["path"] for call in plan.tool_calls] == [".", ".", "."]
    assert provider.to_safe_metadata()["toolplan_normalized"] is True


def test_api_planner_normalizes_common_glob_shapes() -> None:
    content = json.dumps({
        "version": "forgex.toolplan.v2",
        "summary": "find source",
        "tool_calls": [
            {"id": "call_1", "type": "glob_files", "arguments": {"glob": "./src/**/*.cpp"}},
            {"id": "call_2", "type": "glob_files", "arguments": {"path": "**/*"}},
            {"id": "call_3", "type": "list_files", "arguments": {"path": "src/**/*.h"}},
        ],
        "final": False,
    })
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(content),
    )

    plan = provider.request_turn(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("list_files", "glob_files"),
        run_id="run",
        turn=1,
    )

    assert [(call.tool_type, call.arguments) for call in plan.tool_calls] == [
        ("glob_files", {"path": ".", "pattern": "src/**/*.cpp"}),
        ("glob_files", {"path": ".", "pattern": "**/*"}),
        ("glob_files", {"path": ".", "pattern": "src/**/*.h"}),
    ]


def test_api_planner_normalizes_null_build_environment() -> None:
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(tool_plan("build_firmware", {"environment": None, "path": ".", "target": "run"})),
    )

    plan = provider.request_turn(
        task="Build the firmware",
        tool_results=(),
        allowed_tools=("build_firmware",),
        run_id="run",
        turn=1,
    )

    assert plan.tool_calls[0].tool_type == "build_firmware"
    assert plan.tool_calls[0].arguments == {}
    assert provider.to_safe_metadata()["toolplan_normalized"] is True


def test_api_planner_still_rejects_write_root_path_with_diagnostics(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "provider.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(tool_plan("write_file", {"path": ".", "content": "x"})),
    )

    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("write_file",), run_id="run-123", turn=1)

    assert caught.value.classification == ApiPlannerClassification.TOOLPLAN_UNSAFE.value
    assert caught.value.safe_code == "provider_path_unsafe"
    assert provider.to_safe_metadata()["safe_error_code"] == "provider_path_unsafe"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "provider.toolplan.rejected"
    assert events[-1]["error_code"] == "provider_path_unsafe"
    assert events[-1]["tool"] == "write_file"
    assert events[-1]["tool_call_id"] == "call_1"
    assert events[-1]["path"] == "."


def test_api_planner_rejects_forbidden_tool_with_safe_diagnostics(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "provider.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openai"],
        api_key="secret",
        model_id="gpt-test",
        transport=lambda *args: envelope(tool_plan("run_command", {"argv": ["pio", "run"]})),
    )

    with pytest.raises(ApiPlannerError) as caught:
        provider.request_turn(task="Create firmware", tool_results=(), allowed_tools=("list_files",), run_id="run-123", turn=1)

    assert caught.value.safe_code == "provider_requested_forbidden_tool"
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert events[-1]["event"] == "provider.toolplan.rejected"
    assert events[-1]["error_code"] == "provider_requested_forbidden_tool"
    assert events[-1]["tool"] == "run_command"


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


def test_api_planner_repairs_missing_tool_arguments_before_execution() -> None:
    responses = iter(
        (
            envelope(tool_plan("write_file", {})),
            envelope(valid_plan()),
        )
    )
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


def test_toolplan_schema_requires_write_file_path_and_content() -> None:
    from backend.agent_runtime.tool_contracts import ProductToolPlan

    schema = ProductToolPlan.json_schema()
    variants = schema["properties"]["tool_calls"]["items"]["oneOf"]
    write_variant = next(
        item for item in variants if item["properties"]["type"]["const"] == "write_file"
    )

    assert set(write_variant["properties"]["arguments"]["required"]) == {
        "path",
        "content",
    }


def test_api_planner_extracts_json_toolplan_from_wrapped_text() -> None:
    wrapped = "Sure, here is the tool plan:\n" + valid_plan() + "\nI will continue after this."
    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        api_key="secret",
        model_id="model",
        transport=lambda *args: envelope(wrapped),
    )

    plan = provider.request_turn(
        task="Create firmware",
        tool_results=(),
        allowed_tools=("write_file",),
        run_id="run",
        turn=1,
    )

    assert plan.tool_calls[0].path == "src/main.cpp"
    assert provider.outbound_request_count == 1


def test_api_planner_streams_only_safe_summary_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agent_runtime import api_planner_provider as module

    content = json.dumps({
        "version": "forgex.toolplan.v2",
        "summary": "Inspecting GPIO assignments safely",
        "tool_calls": [{"id": "call_1", "type": "read_file", "arguments": {"path": "src/main.cpp"}}],
        "final": False,
    })
    chunks = [content[:35], content[35:60], content[60:95], content[95:]]

    def fake_stream(url, payload, api_key, headers, timeout, callback):
        del url, payload, api_key, headers, timeout
        full = ""
        for chunk in chunks:
            full += chunk
            callback(chunk, full)
        return envelope(full)

    monkeypatch.setattr(module, "stream_chat_completion", fake_stream)
    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="secret", model_id="gpt-test")
    summaries: list[str] = []
    plan = provider.request_turn_stream(
        task="Inspect firmware",
        tool_results=(),
        allowed_tools=("read_file",),
        run_id="run",
        turn=1,
        on_summary=summaries.append,
    )

    assert plan.summary == "Inspecting GPIO assignments safely"
    assert summaries[-1] == plan.summary
    assert all("tool_calls" not in item for item in summaries)
    metadata = provider.to_safe_metadata()
    assert metadata["streaming_used"] is True
    assert metadata["stream_delta_count"] == len(chunks)


def test_partial_json_summary_decoder_handles_incomplete_escapes() -> None:
    from backend.agent_runtime.api_planner_provider import _partial_json_string_value

    assert _partial_json_string_value('{"summary":"hello', "summary") == "hello"
    assert _partial_json_string_value('{"summary":"line\\nnext', "summary") == "line\nnext"
    assert _partial_json_string_value('{"summary":"unicode \\u0041', "summary") == "unicode A"
