"""The October 9 run built successfully but nested verification arguments failed."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend.agent_runtime.api_planner_provider import _toolplan_parse_error
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.task_contract import create_task_contract, repair_task_contract
from backend.agent_runtime.tool_contracts import ProductToolPlan, PRODUCT_TOOLPLAN_VERSION
from backend.contracts.diagnostics import safe_provider_diagnostics
from backend.model_router.models import ModelResponse
from tests.unit.test_native_agent_tools import Router


def check():
    return {"id": "req-1", "status": "implemented",
            "evidence": [{"path": "private-main.cpp", "start_line": 1, "end_line": 10}]}


def parse(checks):
    return ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Verify.", "final": False,
        "tool_calls": [{"id": "verify", "type": "verify_requirements", "arguments": {"checks": checks}}]})


@pytest.mark.parametrize("change,field,constraint", [
    (lambda q: q.update(status="satisfied"), "checks[0].status", "enum"),
    (lambda q: q.update(evidence={"path": "private-main.cpp"}), "checks[0].evidence", "type"),
    (lambda q: q.update(**{"private-extra-field": "private-value"}), "checks[0]", "fields"),
    (lambda q: q["evidence"][0].update(quote="private source quote"), "checks[0].evidence[0]", "fields"),
    (lambda q: q["evidence"][0].update(start_line="1"), "checks[0].evidence[0].start_line", "type"),
    (lambda q: q["evidence"][0].update(start_line=True), "checks[0].evidence[0].start_line", "type"),
    (lambda q: q["evidence"][0].update(end_line=0), "checks[0].evidence[0]", "line_order"),
    (lambda q: q["evidence"][0].update(end_line=641), "checks[0].evidence[0]", "line_span"),
])
def test_nested_rejections_identify_the_field_without_exposing_values(change, field, constraint):
    item = check()
    change(item)
    with pytest.raises(ValueError) as caught:
        parse([item])
    error = _toolplan_parse_error(caught.value)
    assert error.safe_code == "TOOL_ARGUMENTS_INVALID"
    assert error.details["argument_issue"]["field"] == field
    assert error.details["argument_issue"]["constraint"] == constraint
    assert error.details["tool"] == "verify_requirements"
    assert "private" not in json.dumps(error.details)


def test_eighty_lines_remains_valid_and_longer_ranges_must_be_split():
    item = check()
    item["evidence"] = [{"path": "main.cpp", "start_line": 1, "end_line": 80},
                        {"path": "main.cpp", "start_line": 81, "end_line": 160}]
    assert len(parse([item]).tool_calls) == 1


def test_native_repair_gets_nested_constraint_and_one_corrected_check(monkeypatch):
    import backend.agent_runtime.router_planner as module
    events = []
    monkeypatch.setattr(module, "emit_diagnostic_event", lambda **event: events.append(event))
    invalid, valid = check(), check()
    invalid["evidence"][0]["end_line"] = 641
    router = Router([ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
        {"id": "bad", "name": "verify_requirements", "arguments": {"checks": [invalid]}},)),
        ModelResponse("", "nvidia_nim", "native-model", tool_calls=(
        {"id": "good", "name": "verify_requirements", "arguments": {"checks": [valid]}},))])
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="native-model")
    plan = asyncio.run(planner.request_turn_async(task="Verify the saved draft.", tool_results=(),
        allowed_tools=("verify_requirements",), run_id="run", turn=11, stream=False))
    assert len(router.requests) == 2 and plan.tool_calls[0].call_id == "good"
    assert '"field": "checks[0].evidence[0]"' in router.requests[1].system_prompt
    assert "At most 80 lines inclusive" in router.requests[1].system_prompt
    assert "Submit one corrected check first" in router.requests[1].system_prompt
    assert "private-main" not in json.dumps(events)
    assert planner.to_safe_metadata()["last_argument_issue"]["constraint"] == "line_span"
    assert planner.to_safe_metadata()["last_repair_outcome"] == "succeeded"


@pytest.mark.parametrize("prompt", ["Required components.", "Pin connections in a clear table.",
    "Simple circuit/wiring explanation.", "Required libraries and how to install them.",
    "Expected Serial Monitor output.", "Common errors and troubleshooting steps."])
def test_documentation_deliverables_in_the_live_prompt_have_documentation_types(tmp_path, prompt):
    task = create_task_contract(prompt, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert task["requirements"][0]["evidence_kinds"] == ["documentation"]


def test_version_one_continuation_corrects_document_types_preserving_ids_and_history(tmp_path):
    request = "Required components.\nComplete Arduino C++ code.\nIf temperature exceeds 30 C, turn the LED on."
    old = create_task_contract(request, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    old["evidence_contract_version"] = 1
    old["requirements"][0]["evidence_kinds"] = ["source"]
    # Retain inherited evidence whose original section is absent from this request.
    old["requirements"].append({"id": "req-4", "text": "Connection diagram.", "stable_key": "inherited",
                                "evidence_kinds": ["documentation"]})
    original = copy.deepcopy(old)
    new = repair_task_contract(old, workspace=tmp_path)
    from backend.agent_runtime.requirement_evidence import EVIDENCE_CONTRACT_VERSION
    assert old == original and new["evidence_contract_version"] == EVIDENCE_CONTRACT_VERSION
    assert [q["id"] for q in new["requirements"]] == [q["id"] for q in old["requirements"]]
    assert [q["evidence_kinds"] for q in new["requirements"]] == [["documentation"], ["source"], ["source"], ["documentation"]]
    assert new["parent_revision"] == old["revision"]
    assert repair_task_contract(new, workspace=tmp_path) == new


def test_argument_issue_public_diagnostics_ignore_unrecognized_private_keys():
    issue = {"field": "checks[0].evidence[0]", "constraint": "line_span", "expected": "At most 80 lines.", "private-value": "secret"}
    public = safe_provider_diagnostics({"last_argument_issue": issue})
    assert public["last_argument_issue"] == {k: issue[k] for k in ("field", "constraint", "expected")}


def test_rejected_http_requests_have_client_attribution_without_credentials(monkeypatch):
    import backend.api.observability as module
    captured = []
    monkeypatch.setattr(module, "emit_diagnostic_event", lambda **event: captured.append(event))
    request = Request({"type": "http", "method": "GET", "path": "/v1/models", "query_string": b"",
        "scheme": "http", "server": ("127.0.0.1", 8000), "client": ("127.0.0.1", 54321),
        "headers": [(b"user-agent", b"python-httpx/1.0"), (b"authorization", b"Bearer private-credential"),
                    (b"cookie", b"private-cookie")]})
    module._emit_http_diagnostic(request=request, request_id="request", response=SimpleNamespace(status_code=401), duration_ms=0)
    assert captured[0]["client_host"] == "127.0.0.1" and captured[0]["client_port"] == 54321
    assert captured[0]["client_user_agent"] == "python-httpx/1.0"
    assert "private" not in json.dumps(captured)
