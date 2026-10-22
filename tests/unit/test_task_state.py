from backend.agent_runtime.task_state import task_state_v1
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.task_contract import create_task_contract, revise_task_contract


def test_task_state_has_stable_evidence_references_and_recovery_state():
    contract = {"request": "Build an ESP32 sensor with platformio.ini and src/main.cpp", "revision": "rev-2", "parent_revision": "rev-1",
                "requirements": [{"id": "req-1", "text": "Read sensor"}],
                "exclusions": ["Do not flash"], "accepted_choices": ["sensor: DHT22"]}
    observation = {"call_id": "call-7", "tool": "write_file", "status": "completed",
                   "output": {"path": "src/main.cpp", "modified": True}}
    arguments = dict(contract=contract, plan=[{"text": "Compile", "status": "pending"}],
                     observations=[observation], build={"build_id": "build-2", "success": False,
                     "error_code": "COMPILATION_ERROR"}, report={}, repair_history=[],
                     source_revision="source-3", stalled_turns=3, reorientation_started_at=9)
    state = task_state_v1(**arguments)
    assert state["schema_version"] == 1
    assert state["revisions"] == ["rev-1", "rev-2"]
    assert state["expected_artifacts"] == ["platformio.ini", "src/main.cpp"]
    assert state["evidence"][0]["ref"].startswith("tool://call-7/result#sha256/")
    assert state["evidence"][1]["ref"] == "build://build-2"
    assert state["artifact_status"][0]["source_ref"] == "source://src/main.cpp@source-3"
    assert state["blockers"] == ["COMPILATION_ERROR"]
    assert state["recovery_state"]["reorientation_started_at"] == 9
    assert task_state_v1(**arguments) == state


def test_advanced_job_runtime_is_not_clamped_to_standard(tmp_path):
    async def progress(_event, _payload):
        return None

    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
        platformio=object(), progress=progress, max_runtime_seconds=2700,
        job_limits={"elapsed_seconds": 2700, "tool_calls_used": 300})
    assert loop.max_runtime_seconds == 2700
    assert loop.max_tool_calls == 300


def test_configuration_only_checkpoint_tracks_unnamed_source_and_documentation(tmp_path):
    contract = create_task_contract("Build a complete ESP32 soil moisture firmware project using PlatformIO.\n"
        "Provide organized source files, wiring, calibration and a test checklist.\nCompile the firmware.",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    state = task_state_v1(contract=contract, plan=[{"title":"Compile", "status":"pending"}], observations=[], build={}, report={},
        repair_history=[], source_revision="revision", source_paths=("platformio.ini",))
    kinds = {item["kind"]: item["status"] for item in state["deliverable_manifest"]}
    assert kinds["firmware_source"] == "missing"
    assert kinds["project_configuration"] == "present"
    assert kinds["documentation"] == "missing"
    assert "firmware source" in state["next_action"]
    assert "finish the task" not in state["next_action"]
    assert contract["deliverables"]


def test_deliverables_use_current_files_and_never_claim_presence_is_verification(tmp_path):
    contract = create_task_contract("Build ESP32 firmware with platformio.ini and src/main.cpp.\nProvide wiring documentation.",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    state = task_state_v1(contract=contract, plan=[], observations=[{"call_id":"old", "tool":"write_file",
        "status":"completed", "output":{"path":"src/main.cpp"}}], build={"success":True, "workspace_revision":"old"},
        report={}, repair_history=[], source_revision="new", source_paths=("platformio.ini", "README.md"))
    assert next(item for item in state["deliverable_manifest"] if item["kind"] == "firmware_source")["status"] == "missing"
    assert all(item["status"] != "verified" for item in state["deliverable_manifest"])
    revised = revise_task_contract(contract, "Do not build.\nDo not provide wiring documentation.",
        project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    assert not any(item.get("check_kind") == "build" for item in revised["deliverables"])


def test_task_revision_history_survives_multiple_followups(tmp_path):
    args = dict(project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    first = create_task_contract("Build a sensor", **args)
    second = revise_task_contract(first, "Use DHT22", **args)
    third = revise_task_contract(second, "Keep the display local", **args)
    assert third["revision_history"] == [first["revision"], second["revision"], third["revision"]]
    state = task_state_v1(contract=third, plan=[], observations=[], build={}, report={},
                          repair_history=[], source_revision="source-1")
    assert state["revisions"] == third["revision_history"]


def test_explicit_correction_retires_the_old_requirement(tmp_path):
    args = dict(project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    first = create_task_contract("Use GPIO25 for the LED.\nKeep the display local.", **args)
    second = revise_task_contract(first, "Use GPIO18 for the LED instead of GPIO25.", **args)
    assert [item["text"] for item in second["requirements"]] == [
        "Keep the display local.", "Use GPIO18 for the LED instead of GPIO25."
    ]
    assert second["superseded_requirements"][0]["id"] == "req-1"
    assert second["requirements"][-1]["id"] == "req-3"
    assert task_state_v1(contract=second, plan=[], observations=[], build={}, report={},
        repair_history=[], source_revision="source-1")["superseded_requirements"][0]["id"] == "req-1"
    third = revise_task_contract(second, "Do not use GPIO18.", **args)
    assert all("GPIO18" not in item["text"] for item in third["requirements"])
    assert [item["id"] for item in third["superseded_requirements"]] == ["req-1", "req-3"]


def test_explicitly_allowed_feature_retires_old_exclusion(tmp_path):
    args = dict(project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    first = create_task_contract("Do not implement MQTT.\nKeep alarm local.", **args)
    second = revise_task_contract(first, "Implement MQTT now.", **args)
    assert second["exclusions"] == []
    assert second["superseded_exclusions"] == ["Do not implement MQTT."]
    assert any("MQTT" in item["text"] for item in second["requirements"])


def test_new_pin_assignment_replaces_same_component_only(tmp_path):
    args = dict(project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    first = create_task_contract("Use GPIO25 for the LED.\nUse GPIO18 for the button.", **args)
    second = revise_task_contract(first, "Set the LED pin to GPIO26.", **args)
    assert [item["text"] for item in second["requirements"]] == [
        "Use GPIO18 for the button.", "Set the LED pin to GPIO26."
    ]
    assert second["superseded_requirements"][0]["text"] == "Use GPIO25 for the LED."


def test_revision_retires_explicitly_cancelled_acceptance_checks(tmp_path):
    args = dict(project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    first = create_task_contract("Build the firmware.\nRun simulation.\nRun unit tests.", **args)
    assert {check["kind"] for check in first["acceptance_checks"]} == {
        "source_review", "build", "simulation", "unit_tests"
    }
    second = revise_task_contract(
        first, "Do not build.\nSkip the simulation.\nDo not run unit tests.", **args
    )
    assert [check["kind"] for check in second["acceptance_checks"]] == ["source_review"]
