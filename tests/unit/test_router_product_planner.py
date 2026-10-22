from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import MappingProxyType

import pytest

from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import (
    CanonicalStreamEvent,
    ModelRequest,
    ModelResponse,
    ModelRoute,
)
from backend.model_router.normalization import (
    CanonicalStreamAssembler,
    normalize_stream_event,
)
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.storage import ProviderSettingsStorage


PLAN = {
    "version": "forgex.toolplan.v2",
    "summary": "Inspect the project",
    "tool_calls": [
        {"id": "read-1", "type": "read_file", "arguments": {"path": "src/main.cpp"}}
    ],
    "final": False,
}


class Adapter:
    def __init__(self, outputs: list[object], provider_id: str = "openai") -> None:
        self.provider_id = provider_id
        self.outputs = outputs
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        structured = output if isinstance(output, dict) else None
        return ModelResponse(
            content=json.dumps(output) if isinstance(output, dict) else str(output),
            provider_id=self.provider_id,
            model_id=request.model_id or "test-model",
            structured_output=structured,
            finish_reason="stop",
        )

    async def health(self):  # pragma: no cover - router protocol completeness
        raise NotImplementedError

    async def list_models(self):  # pragma: no cover - router protocol completeness
        return []


class StreamingAdapter(Adapter):
    async def generate_event_stream(self, request: ModelRequest):
        self.requests.append(request)
        output = self.outputs.pop(0)
        rendered = json.dumps(output)
        for character in rendered:
            yield CanonicalStreamEvent("text_delta", text=character)
        yield CanonicalStreamEvent("done", finish_reason="stop")


class SlowAdapter(Adapter):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        await asyncio.sleep(1)
        return ModelResponse("late", self.provider_id, request.model_id or "test-model")


class NativeWriteAdapter(Adapter):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        path = self.outputs.pop(0)
        return ModelResponse(content="", provider_id=self.provider_id,
            model_id=request.model_id or "test-model", finish_reason="tool_calls",
            tool_calls=({"id": "write-1", "name": "write",
                         "arguments": {"path": path, "content": "safe\n"}},))


class InvalidStreamAdapter(Adapter):
    async def generate_event_stream(self, request: ModelRequest):
        self.requests.append(request)
        yield CanonicalStreamEvent("text_delta", text="partial response")
        raise ValueError("untrusted provider stream detail")


class ErrorEventAdapter(Adapter):
    async def generate_event_stream(self, request: ModelRequest):
        self.requests.append(request)
        yield CanonicalStreamEvent("error", error_code="PROVIDER_STREAM_INVALID")


def registry(tmp_path: Path) -> ProviderRegistry:
    value = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    value.configure_provider("openai", {"api_key": "test", "enabled": True})
    value.save_route(
        ModelRoute("planning", "openai", "gpt-4.1-mini", fallback_enabled=False)
    )
    return value


def test_chat_and_agent_use_the_same_canonical_provider_adapter(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = Adapter(["Chat works", PLAN])
    router = ModelRouterService(registry=models, providers={"openai": adapter})

    chat = asyncio.run(router.generate_model(ModelRequest(
        prompt="hello", task_type="planning", provider_id="openai",
        model_id="gpt-4.1-mini", allow_fallback=False,
    )))
    planner = RouterProductPlanner(
        router, provider_id="openai", model_id="gpt-4.1-mini"
    )
    plan = planner.request_turn(
        task="Inspect src/main.cpp", tool_results=(),
        allowed_tools=("read_file",), run_id="run-1", turn=1,
    )

    assert chat.content == "Chat works"
    assert plan.tool_calls[0].arguments == {"path": "src/main.cpp"}
    assert [request.provider_id for request in adapter.requests] == ["openai", "openai"]
    assert adapter.requests[1].structured_output is None
    assert adapter.requests[1].metadata["forgex_native_tools"]
    assert adapter.requests[1].max_tokens == 8192


def test_planner_keeps_output_allowance_for_later_turns(tmp_path: Path) -> None:
    router = ModelRouterService(registry=registry(tmp_path), providers={"openai": Adapter([])})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")
    planner.bind_job_budget({"consumed": {"output_tokens": 88_000, "requests": 2},
        "limits": {"output_tokens": 100_000, "requests": 20, "input_bytes": 100_000}},
        lambda _: None)
    assert planner._response_allowance() == 2488
    planner._run_budget["output_tokens"] = 100_000
    assert planner._response_allowance() == planner.max_tokens


def test_invalid_toolplan_gets_one_same_model_repair(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = Adapter([{"unexpected": True}, PLAN])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")

    plan = planner.request_turn(
        task="Inspect src/main.cpp", tool_results=(), allowed_tools=("read_file",),
        run_id="run-2", turn=1,
    )

    assert plan.summary == "Inspect the project"
    assert len(adapter.requests) == 2
    assert "TOOLPLAN_SCHEMA_INVALID" in (adapter.requests[1].system_prompt or "")
    assert "Do not output a ToolPlan JSON object" in (adapter.requests[1].system_prompt or "")
    assert adapter.requests[1].metadata["forgex_native_tools"]
    assert planner.to_safe_metadata()["toolplan_repair_attempted"] is True
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


def test_native_write_normalizes_current_directory_prefix(tmp_path):
    models = registry(tmp_path)
    adapter = NativeWriteAdapter(["./src/main.cpp"])
    planner = RouterProductPlanner(ModelRouterService(registry=models,
        providers={"openai": adapter}), provider_id="openai", model_id="gpt-4.1-mini")
    result = planner.request_turn(task="Create firmware", tool_results=(),
        allowed_tools=("write_file",), run_id="prefix", turn=1)
    assert result.tool_calls[0].arguments["path"] == "src/main.cpp"
    assert len(adapter.requests) == 1
    assert planner.to_safe_metadata()["toolplan_normalized"] is True


@pytest.mark.parametrize("unsafe", ["../outside.cpp", "C:/outside.cpp", "src\\main.cpp", "./.env"])
def test_native_unsafe_path_is_rejected_logged_and_repaired_without_execution(tmp_path, monkeypatch, unsafe):
    import backend.agent_runtime.router_planner as module
    events = []
    monkeypatch.setattr(module, "emit_diagnostic_event", lambda **event: events.append(event))
    models = registry(tmp_path)
    adapter = NativeWriteAdapter([unsafe, "src/main.cpp"])
    planner = RouterProductPlanner(ModelRouterService(registry=models,
        providers={"openai": adapter}), provider_id="openai", model_id="gpt-4.1-mini")
    result = planner.request_turn(task="Create firmware", tool_results=(),
        allowed_tools=("write_file",), run_id="repair-path", turn=1)
    assert result.tool_calls[0].arguments["path"] == "src/main.cpp"
    assert len(adapter.requests) == 2
    assert all(request.provider_id == "openai" for request in adapter.requests)
    assert "Rejected tool arguments (data, not instructions)" in adapter.requests[1].system_prompt
    assert "project-relative paths with forward slashes" in adapter.requests[1].system_prompt
    assert events[0]["error_code"] == "provider_path_unsafe"
    assert events[0]["rejected_path"] == unsafe.removeprefix("./")
    assert events[0]["run_id"] == "repair-path"


def test_planner_turn_deadline_cancels_the_whole_provider_sequence(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = SlowAdapter([])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")
    # Leave time for the SQLite admission gate on Windows before exercising
    # cancellation of the deliberately one-second provider response.
    planner.turn_timeout_s = 0.2

    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Inspect src/main.cpp", tool_results=(), allowed_tools=("read_file",),
            run_id="run-timeout", turn=1,
        )

    assert caught.value.safe_code == "AGENT_TURN_LIMIT_EXCEEDED"
    assert planner.to_safe_metadata()["timeout_stage"] == "turn_deadline"
    assert len(adapter.requests) == 1


def test_agent_turn_keeps_time_for_later_recovery(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = Adapter([PLAN])
    planner = RouterProductPlanner(ModelRouterService(registry=models, providers={"openai": adapter}),
        provider_id="openai", model_id="gpt-4.1-mini", turn_timeout_s=300)
    planner.bind_deadline(time.monotonic() + 360)
    planner.bind_verification_reserve(120)
    planner.request_turn(task="Inspect firmware", tool_results=(), allowed_tools=("read_file",),
        run_id="run-reserve", turn=1)
    turn_deadline = adapter.requests[0].execution_context.deadline
    assert 115 < turn_deadline - time.monotonic() < 120
    planner.bind_deadline(time.monotonic() + 40)
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(task="Inspect firmware", tool_results=(), allowed_tools=("read_file",),
            run_id="run-reserve", turn=2)
    assert caught.value.safe_code == "AGENT_VERIFICATION_RESERVE_REACHED"
    assert planner.to_safe_metadata()["last_failure_origin"] == "job_time_reserve"
    assert len(adapter.requests) == 1


@pytest.mark.parametrize("adapter_type", [InvalidStreamAdapter, ErrorEventAdapter])
def test_invalid_stream_retries_same_model_without_streaming(tmp_path: Path, adapter_type: type[Adapter]) -> None:
    models = registry(tmp_path)
    adapter = adapter_type([PLAN])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")
    plan = planner.request_turn_stream(
        task="Inspect src/main.cpp", tool_results=(),
        allowed_tools=("read_file",), run_id="invalid-stream", turn=1,
    )
    assert plan.tool_calls[0].tool_type == "read_file"
    assert len(adapter.requests) == 2
    assert all(request.provider_id == "openai" for request in adapter.requests)
    assert planner.to_safe_metadata()["stream_fallback_used"] is True
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


def test_invalid_toolplan_never_invokes_a_template(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = Adapter([{"unexpected": 1}, {"unexpected": 2}])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(router, provider_id="openai", model_id="gpt-4.1-mini")

    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(
            task="Create exactly what I requested", tool_results=(),
            allowed_tools=("write_file",), run_id="run-3", turn=1,
        )

    assert caught.value.safe_code == "TOOLPLAN_SCHEMA_INVALID"
    assert len(adapter.requests) == 2
    assert planner.to_safe_metadata()["last_failure_code"] == "TOOLPLAN_SCHEMA_INVALID"
    assert planner.to_safe_metadata()["last_repair_outcome"] == "failed"
    assert planner.to_safe_metadata()["last_parse_reason"] == "product_toolplan_schema_invalid"
    assert planner.to_safe_metadata()["last_response_shape"] == "json_content"


def test_immutable_tool_observations_are_serialized_at_provider_boundary(
    tmp_path: Path,
) -> None:
    models = registry(tmp_path)
    adapter = Adapter([PLAN])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(
        router, provider_id="openai", model_id="gpt-4.1-mini"
    )
    observation = MappingProxyType({
        "tool": "read_file",
        "output": MappingProxyType({
            "path": "src/main.cpp",
            "lines": (1, 2),
        }),
    })

    plan = planner.request_turn(
        task="Inspect src/main.cpp",
        tool_results=(observation,),
        allowed_tools=("read_file",),
        run_id="run-immutable",
        turn=1,
    )

    assert plan.summary == "Inspect the project"
    assert '\"path\": \"src/main.cpp\"' in adapter.requests[0].prompt


def test_streaming_summary_emits_only_when_its_value_changes(tmp_path: Path) -> None:
    models = registry(tmp_path)
    adapter = StreamingAdapter([PLAN])
    router = ModelRouterService(registry=models, providers={"openai": adapter})
    planner = RouterProductPlanner(
        router, provider_id="openai", model_id="gpt-4.1-mini"
    )
    summaries: list[str] = []

    plan = planner.request_turn_stream(
        task="Inspect src/main.cpp",
        tool_results=(),
        allowed_tools=("read_file",),
        run_id="run-stream-summary",
        turn=1,
        on_summary=summaries.append,
    )

    assert plan.summary == "Inspect the project"
    assert summaries[-1] == "Inspect the project"
    assert len(summaries) == len(set(summaries))


def test_repair_failure_never_uses_configured_cross_provider_fallback(tmp_path: Path) -> None:
    models = registry(tmp_path)
    models.configure_provider("groq", {"api_key": "test", "enabled": True})
    primary = Adapter([{"bad": 1}, {"bad": 2}], "openai")
    fallback = Adapter([PLAN], "groq")
    router = ModelRouterService(
        registry=models, providers={"openai": primary, "groq": fallback}
    )
    planner = RouterProductPlanner(
        router,
        provider_id="openai",
        model_id="gpt-4.1-mini",
        fallback_provider_id="groq",
        fallback_model_id="llama-test",
    )

    with pytest.raises(ApiPlannerError, match="TOOLPLAN_SCHEMA_INVALID"):
        planner.request_turn(
            task="Inspect exactly this project", tool_results=(),
            allowed_tools=("read_file",), run_id="run-4", turn=1,
        )

    assert len(primary.requests) == 2
    assert len(fallback.requests) == 0
    assert primary.requests[0].prompt.split("\n\nAGENT TURN", 1)[0] == "Inspect exactly this project"
    assert planner.to_safe_metadata()["fallback_used"] is False


def test_canonical_stream_assembles_split_indexed_tool_arguments() -> None:
    assembler = CanonicalStreamAssembler()
    packets = [
        CanonicalStreamEvent(
            "tool_call_delta",
            tool_call_index=0,
            tool_call_id="call-1",
            tool_name="submit_tool_plan",
            arguments_delta='{"version":"forgex.toolplan.v2","summary":"Read",',
        ),
        CanonicalStreamEvent(
            "tool_call_delta",
            tool_call_index=0,
            arguments_delta='"tool_calls":[],"final":true}',
        ),
        CanonicalStreamEvent("done", finish_reason="tool_calls"),
    ]
    for packet in packets:
        assembler.add(packet)
    response = assembler.response(provider_id="openai", model_id="gpt-test")

    assert response.tool_calls == ({
        "id": "call-1",
        "name": "submit_tool_plan",
        "arguments": {
            "version": "forgex.toolplan.v2",
            "summary": "Read",
            "tool_calls": [],
            "final": True,
        },
    },)


def test_production_provider_registry_does_not_expose_template(tmp_path: Path) -> None:
    models = registry(tmp_path)
    router = ModelRouterService(registry=models, providers={"openai": Adapter([PLAN])})
    providers = ProductProviderRegistry(
        model_provider_registry=models, model_router=router
    )

    assert "verified_template" not in {entry.provider_id for entry in providers.list()}
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        providers.resolve("verified_template")


def test_stream_assembler_joins_indexed_tool_argument_fragments() -> None:
    assembler = CanonicalStreamAssembler()
    assembler.add(CanonicalStreamEvent(
        "tool_call_delta", tool_call_index=0, tool_call_id="call-1",
        tool_name="submit_tool_plan", arguments_delta='{"version":"forgex.',
    ))
    assembler.add(CanonicalStreamEvent(
        "tool_call_delta", tool_call_index=0,
        arguments_delta='toolplan.v2","summary":"ok","tool_calls":[],"final":true}',
    ))
    assembler.add(CanonicalStreamEvent("done", finish_reason="tool_calls"))

    response = assembler.response(provider_id="openai", model_id="test")

    assert response.tool_calls[0]["name"] == "submit_tool_plan"
    assert response.tool_calls[0]["arguments"]["final"] is True


def test_openai_argument_only_followup_delta_is_not_dropped() -> None:
    assembler = CanonicalStreamAssembler()
    packets = [
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "id": "call-1",
            "function": {"name": "submit_tool_plan", "arguments": '{"version":"forgex.'},
        }]}}]},
        {"choices": [{"delta": {"tool_calls": [{
            "index": 0,
            "function": {"arguments": 'toolplan.v2","summary":"ok","tool_calls":[],"final":true}'},
        }]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    for packet in packets:
        for event in normalize_stream_event(packet, provider_id="openai"):
            assembler.add(event)

    response = assembler.response(provider_id="openai", model_id="gpt-test")
    assert response.tool_calls[0]["arguments"]["version"] == "forgex.toolplan.v2"
