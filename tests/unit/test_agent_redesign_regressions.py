from __future__ import annotations

import asyncio
import json
from pathlib import Path
from backend.observability.diagnostics import live_log_path

import pytest

from backend.agent_runtime.observation_context import render_observations
from backend.agent_runtime.tool_contracts import ToolCall, ToolName
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import ToolPermissionPolicy, ToolPolicyError
from backend.api.routes.agent_runtime import _bounded_instruction
from backend.model_router.models import CanonicalStreamEvent, ModelRequest, ModelRoute, RequestExecutionContext
from backend.services.llm_service import LLMConfigurationError, LLMProvider, LLMProviderError
from tests.unit.test_model_runtime_v2 import Provider, build


class StreamProvider(Provider):
    async def generate_event_stream(self, request):
        self.requests.append(request)
        yield CanonicalStreamEvent("text_delta", text="ok")
        yield CanonicalStreamEvent("done", finish_reason="stop")


class FailingStreamProvider(Provider):
    async def generate_event_stream(self, request):
        self.requests.append(request)
        if False:
            yield CanonicalStreamEvent("done")
        raise LLMProviderError(
            "Provider returned error",
            provider=LLMProvider.OPENROUTER,
            status_code=502,
            code="PROVIDER_UNAVAILABLE",
            retryable=True,
        )


def test_local_only_stream_never_contacts_cloud(tmp_path):
    adapter = StreamProvider("openrouter")
    router, _ = build(tmp_path, {"openrouter": adapter})

    async def run():
        return [item async for item in router.stream_model(ModelRequest(prompt="private", local_only=True))]

    with pytest.raises(LLMConfigurationError):
        asyncio.run(run())
    assert adapter.requests == []


def test_router_stream_writes_sanitized_provider_diagnostics(tmp_path, monkeypatch):
    log_path = tmp_path / "live-runtime.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    log_path = live_log_path()
    adapter = FailingStreamProvider("openrouter")
    router, _ = build(tmp_path, {"openrouter": adapter})

    async def run():
        return [
            event
            async for event in router.stream_model(
                ModelRequest(
                    prompt="private project prompt",
                    task_type="code_generation",
                    metadata={"run_id": "run-test", "request_id": "req-test"},
                    execution_context=RequestExecutionContext(attempts={"used": 0, "limit": 1}),
                )
            )
        ]

    with pytest.raises(LLMProviderError):
        asyncio.run(run())

    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "provider.request.started",
        "provider.admission.acquired",
        "provider.request.failed",
    ]
    assert events[-1]["error_code"] == "PROVIDER_UNAVAILABLE"
    assert events[-1]["status"] == 502
    assert events[-1]["retryable"] is True
    serialized = json.dumps(events)
    assert "private project prompt" not in serialized


def test_route_locality_cannot_be_bypassed_by_provider_override(tmp_path):
    adapter = StreamProvider("openrouter")
    router, registry = build(tmp_path, {"openrouter": adapter})
    registry.save_route(ModelRoute("general_chat", "ollama", "local-model", local_only=True))
    request = ModelRequest(prompt="private", provider_id="openrouter", model_id="vendor/model")
    with pytest.raises(LLMConfigurationError):
        asyncio.run(router.generate_model(request))
    assert adapter.requests == []


def test_latest_request_survives_large_conversation():
    latest = "Latest user request: inspect only; do not modify files."
    result = _bounded_instruction("System rules\n" + "old context " * 5000 + "\n" + latest)
    assert result.startswith("System rules")
    assert result.endswith(latest)
    assert len(result.encode("utf-8")) <= 16000


def test_large_observations_remain_valid_json_and_preserve_latest_failure():
    observations = [
        {"call_id": f"read-{i}", "tool": "read_file", "status": "completed", "output": "x" * 60000}
        for i in range(30)
    ]
    observations.append({"call_id": "build", "tool": "build_firmware", "status": "failed", "error": "undefined symbol"})
    rendered = render_observations(observations)
    parsed = json.loads(rendered)
    assert len(rendered) <= 48000
    assert parsed[-1]["error"] == "undefined symbol"
    assert any(item.get("output_truncated") for item in parsed)
    assert parsed[0]["omitted_observations"] > 0


def test_ranged_read_observes_staged_file(tmp_path: Path):
    active = tmp_path / "active"
    stage = tmp_path / "stage"
    active.mkdir()
    stage.mkdir()
    (active / "main.cpp").write_text("old\n", encoding="utf-8")
    (stage / "main.cpp").write_text("first\nsecond\nthird\n", encoding="utf-8")
    executor = ForgeXToolExecutor(write_root=stage, read_root=active, policy=ToolPermissionPolicy())
    result = executor.execute(ToolCall(ToolName.READ_FILE, {"path": "main.cpp", "offset": 2, "limit": 1}))
    assert result["content"].splitlines() == ["second"]
    assert result["next_offset"] == 3
    assert result["total_lines"] == 3
    with pytest.raises(ToolPolicyError):
        executor.execute(ToolCall(ToolName.READ_FILE, {"path": "main.cpp", "offset": True}))


def test_failed_stage_write_preserves_prior_content(tmp_path, monkeypatch):
    from backend.agent_runtime import product_agent_policy
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    call = ToolCall(ToolName.WRITE_FILE, {"path": "main.cpp", "content": "int original;\n"})
    executor.execute(call)
    original = (tmp_path / "main.cpp").read_bytes()
    def fail_sync(_descriptor):
        raise OSError("disk failure")
    monkeypatch.setattr("backend.agent_runtime.tool_executor.os.fsync", fail_sync)
    with pytest.raises(OSError, match="disk failure"):
        executor.execute(ToolCall(ToolName.WRITE_FILE, {"path": "main.cpp", "content": "int replacement;\n"}))
    assert (tmp_path / "main.cpp").read_bytes() == original
    assert list(tmp_path.glob(".forgex-write-*")) == []


def test_valid_platformio_manifest_cannot_regress_to_no_environments(tmp_path):
    from backend.agent_runtime import product_agent_policy

    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    executor.execute(ToolCall(ToolName.WRITE_FILE, {
        "path": "platformio.ini",
        "content": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
    }))

    with pytest.raises(ValueError, match="platformio_environment_section_required"):
        executor.execute(ToolCall(ToolName.WRITE_FILE, {
            "path": "platformio.ini",
            "content": "[platformio]\nsrc_dir = src\n",
        }))

    assert "[env:esp32dev]" in (tmp_path / "platformio.ini").read_text(encoding="utf-8")


def test_ten_file_changeset_survives_stale_edit_and_can_apply_undo(tmp_path):
    from backend.agent_runtime import RuntimeClassification, product_agent_policy
    from backend.agent_runtime.tool_contracts import ProductToolPlan
    from tests.unit.test_tool_runtime import SequencePlanner, make_runtime, plan

    runtime, active, changes = make_runtime(tmp_path)
    stale = ProductToolPlan.parse({
        "version": "forgex.toolplan.v2", "summary": "edit stale context", "final": False,
        "tool_calls": [{"id": "stale", "type": "edit_file_simple", "arguments": {
            "path": "README.txt", "old_text": "does not exist", "new_text": "fixed",
        }}],
    })
    planner = SequencePlanner([
        plan(path="src/module0.cpp", content="int module0() { return 0; }\n"),
        stale,
        *[plan(path=f"src/module{i}.cpp", content=f"int module{i}() {{ return {i}; }}\n", call_id=f"write{i}") for i in range(1, 10)],
        plan(final=True),
    ])
    from backend.agent_runtime import ProductRuntimeLimits
    result = runtime.run_product(task="Create ten modules", planner=planner,
        policy=product_agent_policy(), limits=ProductRuntimeLimits(max_turns=14))
    assert result.classification is RuntimeClassification.PASS
    assert any(item.get("status") == "failed" for item in planner.seen_results[2])
    assert result.created_file_count == 10
    assert not (active / "src/module0.cpp").exists()
    changes.apply(result.change_set_id)
    assert len(list((active / "src").glob("*.cpp"))) == 10
    changes.undo(result.change_set_id)
    assert not list((active / "src").glob("*.cpp"))
