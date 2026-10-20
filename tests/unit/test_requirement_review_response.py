"""Regressions for the successful-build / rejected-review live failure."""
import asyncio
import json
import time

import pytest
import httpx

from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.failure_contract import failure_details
from backend.agent_runtime.review_contract import parse_requirement_review
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.task_contract import create_task_contract, verify_requirement_evidence
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION
from backend.agent_runtime.verification import review_requirements
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import CanonicalStreamEvent, ModelCapabilities, ModelResponse
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.storage import ProviderSettingsStorage
from backend.model_router.providers.nvidia_nim import NvidiaNimProvider, NvidiaNimService
from tests.unit.test_agent_verification_freshness import Builder, progress


REVIEW = {"checks": [{"id": "req-1", "verdict": "satisfied", "reason": "The current source uses GPIO 2."}]}


class ReviewAdapter:
    provider_id = "nvidia_nim"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        request.execution_context.diagnostics.update(
            physical_request_count=1, physical_attempt_id=f"review-attempt-{len(self.requests)}",
            http_status=200, completion_received=True, finish_reason="stop")
        output = self.outputs.pop(0)
        if isinstance(output, ModelResponse):
            return output
        return ModelResponse(content=json.dumps(output) if isinstance(output, dict) else output,
            provider_id=self.provider_id, model_id=request.model_id,
            structured_output=output if isinstance(output, dict) else None, finish_reason="stop")

    async def generate_event_stream(self, request):
        response = await self.generate(request)
        yield CanonicalStreamEvent("text_delta", text=response.text)
        yield CanonicalStreamEvent("done", finish_reason=response.finish_reason)


def planner_for(tmp_path, monkeypatch, outputs, *, native=True, json_schema=False):
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "models.json", credential_store=MemoryCredentialStore()))
    registry.configure_provider("nvidia_nim", {"api_key": "test", "enabled": True})
    monkeypatch.setattr(registry, "model_capabilities", lambda *_: ModelCapabilities(native_tools=native, json_schema=json_schema))
    adapter = ReviewAdapter(outputs)
    planner = RouterProductPlanner(ModelRouterService(registry=registry, providers={"nvidia_nim": adapter}),
        provider_id="nvidia_nim", model_id="nvidia/nemotron-3-super-120b-a12b")
    return planner, adapter


def source_report(tmp_path):
    (tmp_path / "main.cpp").write_text("const int LED_PIN = 2;\n", encoding="utf-8")
    contract = create_task_contract("Use LED GPIO 2.", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    report = verify_requirement_evidence(contract, [{"id": "req-1", "status": "implemented",
        "evidence": [{"path": "main.cpp", "quote": "const int LED_PIN = 2;"}]}], tmp_path, "revision-1")
    return contract, report


@pytest.mark.parametrize("native,json_schema", [(True, False), (False, True), (False, False)])
def test_review_json_passes_through_canonical_router_without_toolplan_repair(tmp_path, monkeypatch, native, json_schema):
    planner, adapter = planner_for(tmp_path, monkeypatch, [REVIEW], native=native, json_schema=json_schema)
    contract, report = source_report(tmp_path)
    result = asyncio.run(review_requirements(planner, contract, report,
        lambda path: (tmp_path / path).read_text(), "revision-1", deadline=time.monotonic() + 60, run_id="live-regression"))
    assert result["valid"] and result["verification_level"] == "model_review"
    assert not result["semantic_review"]["execution_verified"]
    assert not result["hardware_verified"]
    assert len(adapter.requests) == 1
    request = adapter.requests[0]
    assert request.structured_output.name == "requirement_review"
    assert request.structured_output.json_schema["properties"]["checks"]["items"]["properties"]["id"]["enum"] == ["req-1"]
    assert not request.metadata.get("forgex_native_tools")
    assert "Do not output a ToolPlan" not in request.system_prompt  # No conflicting generation instructions.
    assert "do not return a ToolPlan envelope" in request.system_prompt
    assert planner.to_safe_metadata()["physical_request_count"] == 1
    assert planner.to_safe_metadata()["response_mode"] == "requirement_review"
    assert planner.to_safe_metadata()["toolplan_repair_attempted"] is False


def test_invalid_review_gets_one_review_schema_repair(tmp_path, monkeypatch):
    planner, adapter = planner_for(tmp_path, monkeypatch, [{"checks": []}, REVIEW])
    plan = asyncio.run(planner.for_role("review").request_review_async(requirement_ids=["req-1"],
        task="Review GPIO usage.", tool_results=(), allowed_tools=(), run_id="repair:semantic-review", turn=1))
    assert plan.final and json.loads(plan.summary) == REVIEW
    assert len(adapter.requests) == 2
    assert all(item.structured_output.name == "requirement_review" for item in adapter.requests)
    assert "SEMANTIC_REVIEW_INCOMPLETE" in adapter.requests[1].system_prompt
    assert "corrected review JSON" in adapter.requests[1].system_prompt
    metadata = planner.to_safe_metadata()
    assert metadata["initial_failure_code"] == "SEMANTIC_REVIEW_INCOMPLETE"
    assert metadata["last_repair_outcome"] == "succeeded"
    assert metadata["physical_request_count"] == 2


def test_truncated_review_repairs_with_review_instructions(tmp_path, monkeypatch):
    truncated = ModelResponse(content='{"checks":[', provider_id="nvidia_nim",
        model_id="nvidia/nemotron-3-super-120b-a12b", finish_reason="length")
    planner, adapter = planner_for(tmp_path, monkeypatch, [truncated, REVIEW])
    plan = asyncio.run(planner.for_role("review").request_review_async(requirement_ids=["req-1"],
        task="Review GPIO usage.", tool_results=(), allowed_tools=(), run_id="truncated-review", turn=1))
    assert json.loads(plan.summary) == REVIEW
    assert len(adapter.requests) == 2
    assert "corrected review JSON" in adapter.requests[1].system_prompt
    assert "Return one complete next action" not in adapter.requests[1].system_prompt
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


def test_large_source_units_use_subset_schemas_before_full_aggregate(tmp_path, monkeypatch):
    padding = "// source span padding\n" * 3500
    sources = {"main.cpp": "const int LED_PIN = 2;\n" + padding,
               "timer.cpp": "const int LED_INTERVAL = 500;\n" + padding}
    for path, source in sources.items():
        (tmp_path / path).write_text(source, encoding="utf-8")
    contract = create_task_contract("Use LED GPIO 2.\nSet the LED interval to 500 ms.",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert [item["id"] for item in contract["requirements"]] == ["req-1", "req-2"]
    report = verify_requirement_evidence(contract, [
        {"id": "req-1", "status": "implemented", "evidence": [{"path": "main.cpp", "quote": "const int LED_PIN = 2;"}]},
        {"id": "req-2", "status": "implemented", "evidence": [{"path": "timer.cpp", "quote": "const int LED_INTERVAL = 500;"}]},
    ], tmp_path, "large-revision")
    second = {"checks": [{"id": "req-2", "verdict": "satisfied", "reason": "The source sets the interval to 500 ms."}]}
    aggregate = {"checks": REVIEW["checks"] + second["checks"]}
    planner, adapter = planner_for(tmp_path, monkeypatch, [REVIEW, REVIEW, second, second, aggregate])
    result = asyncio.run(review_requirements(planner, contract, report, sources.__getitem__, "large-revision",
        deadline=time.monotonic() + 60, run_id="large-review"))
    assert result["valid"] and result["semantic_review"]["coverage_complete"]
    assert len(result["semantic_review"]["units"]) == 4
    expected_ids = [request.structured_output.json_schema["properties"]["checks"]["items"]["properties"]["id"]["enum"]
                    for request in adapter.requests]
    assert expected_ids == [["req-1"], ["req-1"], ["req-2"], ["req-2"], ["req-1", "req-2"]]
    assert planner.to_safe_metadata()["physical_request_count"] == 5


def test_failed_review_preserves_its_attempt_and_parse_diagnostics(tmp_path, monkeypatch):
    planner, adapter = planner_for(tmp_path, monkeypatch, [{"checks": []}, {"unexpected": True}])
    planner._metadata.update(outbound_request_count=11, physical_request_count=11, physical_attempt_id="main-turn-11")
    with pytest.raises(ApiPlannerError, match="SEMANTIC_REVIEW_INVALID"):
        asyncio.run(planner.for_role("review").request_review_async(requirement_ids=["req-1"],
            task="Review GPIO usage.", tool_results=(), allowed_tools=(), run_id="failed:semantic-review", turn=1))
    metadata = planner.to_safe_metadata()
    assert metadata["physical_request_count"] == metadata["outbound_request_count"] == 13
    assert metadata["physical_attempt_id"] == "review-attempt-2"
    assert metadata["initial_failure_code"] == "SEMANTIC_REVIEW_INCOMPLETE"
    assert metadata["recovery_failure_code"] == "SEMANTIC_REVIEW_INVALID"
    assert metadata["last_parse_reason"] == "SEMANTIC_REVIEW_INVALID"
    assert metadata["last_failure_origin"] == "semantic_review"
    failure = failure_details("AGENT_REVIEW_REQUIRED", status="failed", evidence={"diagnostics": metadata})
    assert failure["category"] == "invalid_model_output"
    assert failure["primary_code"] == "SEMANTIC_REVIEW_INCOMPLETE"
    assert failure["physical_attempt_id"] == "review-attempt-2"


def test_multiple_review_units_do_not_double_count_attempts(tmp_path, monkeypatch):
    planner, adapter = planner_for(tmp_path, monkeypatch, [REVIEW, REVIEW])
    reviewer = planner.for_role("review")
    for index in range(2):
        asyncio.run(reviewer.request_review_async(requirement_ids=["req-1"], task="Review GPIO usage.",
            tool_results=(), allowed_tools=(), run_id=f"unit-{index}", turn=1))
    assert planner.to_safe_metadata()["physical_request_count"] == 2
    assert planner.to_safe_metadata()["outbound_request_count"] == 2


@pytest.mark.parametrize("payload", [
    {"checks": []},
    {"checks": REVIEW["checks"] * 2},
    {"checks": [{"id": "foreign-id", "verdict": "satisfied", "reason": "Unknown requirement."}]},
    {"checks": [{"id": "req-1", "verdict": "pass", "reason": "Unsupported verdict."}]},
    {"checks": [{"id": "req-1", "verdict": "satisfied", "reason": ""}]},
    {"checks": [{"id": "req-1", "verdict": "satisfied", "reason": "x" * 2001}]},
    '{"checks":[],"checks":[]}',
    '{"checks":NaN}',
    "I think it looks good.",
    {"version": PRODUCT_TOOLPLAN_VERSION, "summary": json.dumps(REVIEW), "tool_calls": [], "final": False},
])
def test_review_never_accepts_incomplete_or_invalid_verdicts(payload):
    with pytest.raises(ValueError, match="SEMANTIC_REVIEW_"):
        parse_requirement_review(payload, ["req-1"])


def test_legacy_final_review_envelope_remains_compatible():
    plan = parse_requirement_review({"version": PRODUCT_TOOLPLAN_VERSION,
        "summary": json.dumps(REVIEW), "tool_calls": [], "final": True}, ["req-1"])
    assert plan.final and not plan.tool_calls and json.loads(plan.summary) == REVIEW


def test_review_cannot_invoke_native_tools(tmp_path, monkeypatch):
    malicious = ModelResponse(content=json.dumps(REVIEW), provider_id="nvidia_nim",
        model_id="nvidia/nemotron-3-super-120b-a12b", tool_calls=({"id": "write", "name": "write_file",
        "arguments": {"path": "main.cpp", "content": "tampered"}},), finish_reason="tool-calls")
    planner, _ = planner_for(tmp_path, monkeypatch, [malicious, malicious])
    with pytest.raises(ApiPlannerError, match="SEMANTIC_REVIEW_INVALID"):
        asyncio.run(planner.request_review_async(requirement_ids=["req-1"], task="Review GPIO usage.",
            tool_results=(), allowed_tools=(), run_id="tools-denied", turn=1))
    assert not (tmp_path / "main.cpp").exists()


@pytest.mark.parametrize("native,json_schema", [(True, False), (False, True), (False, False)])
def test_nvidia_http_review_uses_the_declared_result_schema(tmp_path, monkeypatch, native, json_schema):
    planner, _ = planner_for(tmp_path, monkeypatch, [], native=native, json_schema=json_schema)
    captured = []
    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        if native:
            assert payload["tool_choice"]["function"]["name"] == "requirement_review"
            assert payload["tools"][0]["function"]["parameters"]["required"] == ["checks"]
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "review-result", "type": "function",
                "function": {"name": "requirement_review", "arguments": json.dumps(REVIEW)}}]}
        else:
            assert not payload.get("tools")
            if json_schema:
                assert payload["response_format"]["json_schema"]["name"] == "requirement_review"
            message = {"role": "assistant", "content": json.dumps(REVIEW)}
        return httpx.Response(200, json={"model": planner.model_id,
            "choices": [{"message": message, "finish_reason": "tool_calls" if native else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = NvidiaNimProvider(planner.router.registry)
            monkeypatch.setattr(provider, "_service", lambda model_id=None: NvidiaNimService(
                api_key="test", model=model_id or planner.model_id, client=client))
            planner.router = ModelRouterService(registry=planner.router.registry, providers={"nvidia_nim": provider})
            reviewer = planner.for_role("review")
            return await reviewer.request_review_async(requirement_ids=["req-1"], task="Review GPIO usage.",
                tool_results=(), allowed_tools=(), run_id="http-review", turn=1)
    plan = asyncio.run(scenario())
    assert json.loads(plan.summary) == REVIEW and not plan.tool_calls and plan.final
    assert len(captured) == 1
    assert planner.to_safe_metadata()["physical_request_count"] == 1


def test_unverified_review_keeps_requirement_unverified(tmp_path, monkeypatch):
    review = {"checks": [{"id": "req-1", "verdict": "unverified", "reason": "GPIO implementation evidence is insufficient."}]}
    planner, _ = planner_for(tmp_path, monkeypatch, [review])
    contract, report = source_report(tmp_path)
    result = asyncio.run(review_requirements(planner, contract, report, lambda path: (tmp_path / path).read_text(),
        "revision-1", deadline=time.monotonic() + 60, run_id="unverified"))
    assert not result["valid"] and result["checks"][0]["status"] == "unverified"


def test_autonomous_run_finishes_after_build_and_bare_review_json(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = create_task_contract("Use LED GPIO 2.", project_id="p", session_id="s", workspace=workspace, intent="generate")
    def plan(calls=()):
        return {"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Inspect and verify GPIO usage.", "tool_calls": list(calls), "final": not calls}
    outputs = [
        plan([{"id": "source", "type": "write_file", "arguments": {"path": "main.cpp", "content": "const int LED_PIN = 2;\n"}}]),
        plan([{"id": "build", "type": "build_firmware", "arguments": {}}]),
        plan([{"id": "requirements", "type": "verify_requirements", "arguments": {"checks": [{"id": "req-1", "status": "implemented",
            "evidence": [{"path": "main.cpp", "quote": "const int LED_PIN = 2;"}]}]}}]),
        plan(), REVIEW,
    ]
    planner, adapter = planner_for(tmp_path, monkeypatch, outputs)
    loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=Builder(), progress=progress, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=True))
    assert result.success, (result.message, result.requirement_report, loop.observations[-3:])
    assert result.build_verified and result.requirement_report["valid"]
    assert len(adapter.requests) == 5
    assert result.provider_diagnostics["physical_request_count"] == 5
