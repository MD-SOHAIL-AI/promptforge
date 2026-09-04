from __future__ import annotations

from backend.agent_runtime.activity import AgentActivity, activity_for_run_event, run_activity_fields


def test_runtime_event_activity_mapping_is_safe_and_high_level() -> None:
    assert activity_for_run_event({"event_type": "build_started", "status": "running", "stage": "build"}) is AgentActivity.BUILDING
    assert activity_for_run_event({"event_type": "build_failed", "status": "running", "stage": "build"}) is AgentActivity.DIAGNOSING
    assert activity_for_run_event({"event_type": "build_repair_started", "status": "running", "stage": "repair"}) is AgentActivity.REPAIRING
    assert activity_for_run_event({"event_type": "flash.confirmed", "status": "running", "stage": "flash"}) is AgentActivity.CONNECTING
    assert activity_for_run_event({"event_type": "flash.started", "status": "running", "stage": "flash"}) is AgentActivity.FLASHING


def test_run_activity_fields_include_backend_label() -> None:
    fields = run_activity_fields({"event_type": "tool.completed", "status": "running", "tool": "write_file"})

    assert fields == {
        "activity": "editing",
        "activity_label": "Shaping",
        "activity_phase": "active",
    }
