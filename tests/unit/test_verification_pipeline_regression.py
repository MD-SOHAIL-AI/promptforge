"""Regression coverage for the ESP32 temperature-monitor verification failure."""
import asyncio
import copy
import hashlib
import json
import time

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.engine import PhaseBudget
from backend.agent_runtime.task_contract import create_task_contract, repair_task_contract
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ToolCall, ToolName
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.agent_runtime.verification import review_requirements
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_requirement_review_response import planner_for, source_report


TEMPERATURE_REQUEST = """Build a simple beginner-friendly ESP32 project using Arduino IDE.

Project: ESP32 Automatic Temperature Monitor

Requirements:
Use an ESP32 development board.
Use a DHT11 temperature and humidity sensor.
Use one LED as a temperature warning indicator.
Read temperature and humidity every 2 seconds.
Print the readings in the Serial Monitor.
If the temperature goes above 30°C, turn the LED ON.
If the temperature is 30°C or below, turn the LED OFF.
Provide:
Required components.
Pin connections in a clear table.
Simple circuit/wiring explanation.
Complete Arduino C++ code.
Required libraries and how to install them.
Step-by-step instructions to upload the code to ESP32.
Explanation of the code for a beginner.
Expected Serial Monitor output.
Common errors and troubleshooting steps.
Keep the circuit and code simple. Avoid unnecessary classes or advanced programming concepts.
"""

SOURCE = """#include <Arduino.h>
#include <DHT.h>
constexpr int LED_PIN = 2;
DHT sensor(4, DHT11);
void setup() { Serial.begin(115200); pinMode(LED_PIN, OUTPUT); sensor.begin(); }
void loop() {
  const float temperature = sensor.readTemperature();
  const float humidity = sensor.readHumidity();
  if (!isnan(temperature) && !isnan(humidity)) {
    Serial.print(temperature); Serial.print(" "); Serial.println(humidity);
    digitalWrite(LED_PIN, temperature > 30.0f ? HIGH : LOW);
  }
  delay(2000);
}
"""
DOCUMENT = """# Components
ESP32, DHT11, LED, 220 ohm resistor, breadboard and wires.
# Pin connections
| Component | ESP32 pin |
| --- | --- |
| DHT11 data | GPIO4 |
| LED anode via resistor | GPIO2 |
Power the sensor from 3.3V and connect all grounds together; LED cathode goes to ground.
# Libraries and upload
In Arduino IDE Boards Manager install esp32 by Espressif Systems. In Library Manager
install DHT sensor library by Adafruit and Adafruit Unified Sensor. Select ESP32 Dev Module
and the correct serial port. Copy main.cpp into an Arduino sketch and upload via USB.
# Explanation
setup initializes serial output, LED and sensor; loop reads every two seconds and
switches the LED on above 30 degrees, off otherwise. Invalid readings are skipped.
# Expected output
25.0 45.0 (temperature in Celsius and relative humidity in percent).
# Troubleshooting
Check GPIO4 and ground wiring for failed readings; check USB cable, board and port
if upload fails. Install both libraries if compilation reports missing DHT.h.
"""


def temperature_contract(workspace):
    return create_task_contract(TEMPERATURE_REQUEST, project_id="p", session_id="s", workspace=workspace, intent="generate")


def test_short_temperature_request_has_real_obligations_and_context(tmp_path):
    contract = temperature_contract(tmp_path)
    assert len(contract["requirements"]) == 18
    assert contract["request_context"] == ["Project: ESP32 Automatic Temperature Monitor", "Requirements:", "Provide:"]
    assert contract["request"] == TEMPERATURE_REQUEST
    by_text = {item["text"]: item for item in contract["requirements"]}
    assert by_text["Complete Arduino C++ code."]["evidence_kinds"] == ["source"]
    assert by_text["Required components."]["evidence_kinds"] == ["documentation"]
    assert by_text["Step-by-step instructions to upload the code to ESP32."]["evidence_kinds"] == ["documentation"]
    assert "unnecessary classes" in contract["requirements"][-1]["text"]


def test_saved_short_contract_migrates_once_preserving_real_ids_and_history(tmp_path):
    old = temperature_contract(tmp_path)
    lines = [line for line in TEMPERATURE_REQUEST.splitlines() if line.strip()]
    old.update(partition_version=4, requirements=[{"id": f"req-{i}", "text": text,
        "stable_key": hashlib.sha256(text.encode()).hexdigest()} for i, text in enumerate(lines, 1)])
    old.pop("request_context")
    snapshot = copy.deepcopy(old)
    repaired = repair_task_contract(old, workspace=tmp_path)
    assert old == snapshot
    assert len(repaired["requirements"]) == 18
    assert next(item for item in repaired["requirements"] if item["text"].startswith("Use a DHT11"))["id"] == "req-5"
    assert {item["id"] for item in repaired["superseded_requirements"]} == {"req-2", "req-3", "req-11"}
    assert repaired["parent_revision"] == old["revision"]
    assert old["revision"] in repaired["revision_history"]
    assert repaired["revision_history"][-1] == repaired["revision"]
    assert repair_task_contract(repaired, workspace=tmp_path) == repaired


def test_numbered_reads_use_current_overlay_and_paginate_without_missing_lines(tmp_path):
    active, staged = tmp_path / "active", tmp_path / "stage"
    active.mkdir()
    staged.mkdir()
    (active / "main.cpp").write_text("stale source\n")
    source = "// introduction\n\n" + "".join(f"const int value_{i} = {i};\n" for i in range(600))
    (staged / "main.cpp").write_text(source, newline="\n")
    executor = ForgeXToolExecutor(read_root=active, write_root=staged, policy=product_agent_policy())
    content, offset = "", 1
    while offset:
        result = executor.execute(ToolCall(tool=ToolName.READ_FILE, arguments={"path": "main.cpp", "offset": offset, "limit": 2000}))
        assert result["content_sha256"] == hashlib.sha256(source.encode()).hexdigest()
        assert result["numbered_content"].startswith(f"{offset}: ")
        assert len(json.dumps(result)) < 16000
        assert not result["truncated_lines"]
        content += result["content"]
        offset = result["next_offset"]
    assert content == source
    default = executor.execute(ToolCall(tool=ToolName.READ_FILE, arguments={"path": "main.cpp"}))
    assert default["numbered_content"].startswith("1: // introduction\n2: \n3: const int value_0")


@pytest.mark.parametrize("native,json_schema", [(True, False), (False, True), (False, False)])
def test_temperature_workflow_passes_both_canonical_review_gates(tmp_path, monkeypatch, native, json_schema):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = temperature_contract(workspace)
    checks = []
    for requirement in contract["requirements"]:
        docs = requirement["evidence_kinds"] == ["documentation"]
        checks.append({"id": requirement["id"], "status": "implemented", "evidence": [
            {"path": "README.md" if docs else "main.cpp", "start_line": 1, "end_line": len((DOCUMENT if docs else SOURCE).splitlines())}]})
    review = {"checks": [{"id": item["id"], "verdict": "satisfied", "reason": "Current implementation and guide satisfy this requirement; the host supplied complete current files and build evidence."} for item in contract["requirements"]]}
    assert len(json.dumps(review)) > 1200  # The old child summary would truncate this review.
    def plan(calls=()):
        return {"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Temperature monitor ready for review.", "tool_calls": list(calls), "final": not calls}
    outputs = [plan([
        {"id": "source", "type": "write_file", "arguments": {"path": "main.cpp", "content": SOURCE}},
        {"id": "guide", "type": "write_file", "arguments": {"path": "README.md", "content": DOCUMENT}},
    ]), plan([{"id": "build", "type": "build_firmware", "arguments": {}}])]
    outputs.extend(plan([{"id": f"references-{i}", "type": "verify_requirements", "arguments": {"checks": checks[i:i+8]}}]) for i in range(0, len(checks), 8))
    outputs.extend([plan(), review, review])
    planner, adapter = planner_for(tmp_path, monkeypatch, outputs, native=native, json_schema=json_schema)
    events = []
    async def capture(event, payload):
        events.append((event, payload))
    loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=Builder(), progress=capture, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=True))
    assert result.success, (result.classification, result.message, result.requirement_report)
    assert result.build_verified and result.requirement_report["valid"]
    completion = result.requirement_report["completion_review"]
    assert completion["passed"] and completion["review"]["scope"] == "completion"
    assert not completion["hardware_verified"]
    assert len(adapter.requests) == 8
    assert all(request.structured_output.name == "requirement_review" for request in adapter.requests[-2:])
    assert "independent completion review" in adapter.requests[-1].prompt
    assert [payload["phase"] for event, payload in events if event == "VERIFICATION_STARTED"] == ["requirement_review", "completion_review"]


def test_completion_review_rejects_semantic_failure_even_after_first_review_passes(tmp_path, monkeypatch):
    contract, report = source_report(tmp_path)
    review = {"checks": [{"id": "req-1", "verdict": "unverified", "reason": "Declaration alone does not show LED behavior."}]}
    planner, adapter = planner_for(tmp_path, monkeypatch, [review])
    result = asyncio.run(review_requirements(planner, contract, report, lambda path: (tmp_path / path).read_text(),
        "revision-1", deadline=time.monotonic() + 60, run_id="completion-negative", scope="completion"))
    assert not result["valid"] and result["checks"][0]["review_reason"] == review["checks"][0]["reason"]
    assert len(adapter.requests) == 1


def test_complex_review_reserves_both_calls_and_schema_repairs(tmp_path, monkeypatch):
    planner, _ = planner_for(tmp_path, monkeypatch, [])
    planner.bind_review_budget(requirement_count=18, review_count=2)
    assert planner._run_budget["_phase_reserve"]["requests"] == 4
    assert planner._run_budget["_phase_reserve"]["output_tokens"] >= 4 * 18 * 200
    assert PhaseBudget.for_task(True, review_count=2).generation_reserve == 240
    reserve = PhaseBudget.for_task(True, [500], review_count=2).generation_reserve
    planner.bind_verification_reserve(reserve)
    assert reserve == 420 and planner._verification_reserve_s == reserve


def test_literal_headings_inside_examples_remain_requirement_content(tmp_path):
    contract = create_task_contract("Provide this exact README example:\n```text\nProject: Example\nRequirements:\nProvide:\n```",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert not contract["request_context"]
    assert "Project: Example\nRequirements:\nProvide:" in contract["requirements"][0]["text"]


def test_resume_revalidates_saved_citations_and_does_not_reuse_review_or_build(tmp_path):
    from backend.agent_runtime.task_contract import verify_requirement_evidence
    from tests.unit.test_tool_runtime import SequencePlanner
    contract = create_task_contract("Use LED GPIO 2.\nInclude README instructions.", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    (tmp_path / "main.cpp").write_text("int led = 2;\n")
    (tmp_path / "README.md").write_text("Connect LED to GPIO 2.\n")
    checks = [{"id":"req-1", "status":"implemented", "evidence":[{"path":"main.cpp","quote":"int led = 2;"}]},
              {"id":"req-2", "status":"implemented", "evidence":[{"path":"README.md","quote":"Old instructions."}]}]
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=SequencePlanner([]), platformio=Builder(), progress=progress,
        task_contract=contract, resume_checkpoint={"requirement_checks":checks})
    assert not loop.build_passed
    assert loop.requirement_report["verification_level"] == "source_reference"
    assert [item["status"] for item in loop.requirement_report["checks"]] == ["referenced", "unverified"]
    (tmp_path / "README.md").write_text("Old instructions.\n")
    loop._invalidate_verification()
    assert loop.requirement_report["references_valid"]
    assert not loop.requirement_report["valid"]
    (tmp_path / "README.md").unlink()
    loop._invalidate_verification()
    assert loop.requirement_report["missing"] == ["req-2"]
    assert loop.requirement_report["checks"][0]["status"] == "referenced"


def test_large_unicode_read_is_bounded_and_citation_metadata_survives_rendering(tmp_path):
    from backend.agent_runtime.observation_context import render_observations
    (tmp_path / "README.md").write_text("温" * 10000 + "\nnext line\n", encoding="utf-8")
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    result = executor.execute(ToolCall(tool=ToolName.READ_FILE, arguments={"path":"README.md"}))
    assert result["truncated_lines"] == [1] and result["next_offset"] == 2
    assert len(json.dumps(result)) < 14000
    rendered = json.loads(render_observations([{"call_id":"read", "tool":"read_file", "status":"completed", "output":result}],max_chars=16000))
    assert rendered[0]["output"]["content_sha256"] == result["content_sha256"]
    assert rendered[0]["output"]["truncated_lines"] == [1]


@pytest.mark.parametrize("handoff", [False, True])
def test_malformed_completion_response_reports_its_phase_and_keeps_first_review(tmp_path, monkeypatch, handoff):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.cpp").write_text("const int LED_PIN = 2;\n")
    contract = create_task_contract("\n".join(f"{i}. Use LED GPIO 2." for i in range(1, 9)),
        project_id="p", session_id="s", workspace=workspace, intent="edit")
    checks = [{"id":item["id"], "status":"implemented", "evidence":[{"path":"main.cpp", "quote":"const int LED_PIN = 2;"}]} for item in contract["requirements"]]
    first_review = {"checks":[{"id":item["id"], "verdict":"satisfied", "reason":"Current source supplies the requested pin."} for item in contract["requirements"]]}
    planner, adapter = planner_for(tmp_path, monkeypatch, [
        {"version":PRODUCT_TOOLPLAN_VERSION, "summary":"Check source", "final":False,
         "tool_calls":[{"id":"references", "type":"verify_requirements", "arguments":{"checks":checks}}]},
        ({"version":PRODUCT_TOOLPLAN_VERSION, "summary":"Submit unchanged evidence", "final":False,
          "tool_calls":[{"id":"references-again", "type":"verify_requirements", "arguments":{"checks":checks}}]}
         if handoff else {"version":PRODUCT_TOOLPLAN_VERSION, "summary":"Ready", "final":True, "tool_calls":[]}),
        first_review, {"checks":[]}, {"checks":[]},
    ])
    loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=Builder(), progress=progress, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=False))
    assert not result.success and result.classification == "AGENT_REVIEW_REQUIRED"
    assert result.requirement_report["valid"]  # Preserve the first gate's evidence.
    completion = result.requirement_report["completion_review"]
    assert not completion["passed"] and completion["failure_code"] == "SEMANTIC_REVIEW_INCOMPLETE"
    assert "completion review" in result.task_state["next_action"].lower()
    assert len(adapter.requests) == 5
