from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.agent_runtime.turn_router import AgentTurnContext, AgentTurnIntent, AgentTurnRouter, approved_plan_execution_decision
from backend.api.routes.agent_runtime import _failed_run_continuation


@pytest.mark.parametrize(
    ("content", "intent", "starts_run"),
    [
        ("hi", AgentTurnIntent.CHAT, False),
        ("hello", AgentTurnIntent.CHAT, False),
        ("what model are you", AgentTurnIntent.CHAT, False),
        ("what provider are you using", AgentTurnIntent.CHAT, False),
        ("what can you do", AgentTurnIntent.CHAT, False),
        ("what is PWM", AgentTurnIntent.CHAT, False),
        ("what happens when I flash an ESP32", AgentTurnIntent.CHAT, False),
        ("how do I build a PlatformIO project", AgentTurnIntent.CHAT, False),
        ("explain src/main.cpp", AgentTurnIntent.INSPECT, False),
        ("show me main.cpp", AgentTurnIntent.INSPECT, False),
        ("what does line 25 do", AgentTurnIntent.INSPECT, False),
        ("why did the last build fail", AgentTurnIntent.INSPECT, False),
        ("change delay to 250ms", AgentTurnIntent.EDIT, True),
        ("add Wi-Fi support", AgentTurnIntent.EDIT, True),
        ("create an ESP32 blink project", AgentTurnIntent.GENERATE, True),
        ("build it", AgentTurnIntent.BUILD, True),
        ("compile this project", AgentTurnIntent.BUILD, True),
        ("simulate this ESP32 project", AgentTurnIntent.SIMULATE, True),
        ("run the simulation", AgentTurnIntent.SIMULATE, True),
        ("fix the build", AgentTurnIntent.REPAIR, True),
        ("fix the current compiler error", AgentTurnIntent.REPAIR, True),
        ("flash it", AgentTurnIntent.FLASH, False),
        ("open serial monitor", AgentTurnIntent.MONITOR, False),
        ("stop serial monitor", AgentTurnIntent.STOP_MONITOR, False),
        ("cancel", AgentTurnIntent.CANCEL, False),
        ("stop", AgentTurnIntent.CANCEL, False),
        ("lorem ipsum qqq", AgentTurnIntent.UNKNOWN, False),
    ],
)
def test_turn_router_classifies_without_accidental_execution(content: str, intent: AgentTurnIntent, starts_run: bool) -> None:
    decision = AgentTurnRouter().route(content)

    assert decision.intent is intent
    assert decision.starts_product_run is starts_run


@pytest.mark.parametrize(
    "content",
    [
        "how does flashing work?",
        "explain what the build command does",
        "why would I use serial monitor?",
    ],
)
def test_turn_router_keyword_traps_stay_conversational(content: str) -> None:
    decision = AgentTurnRouter().route(content)

    assert decision.intent is AgentTurnIntent.CHAT
    assert decision.starts_product_run is False


def test_project_requirements_cannot_trigger_serial_monitor_control() -> None:
    request = (
        "Create a complete production-style ESP32 multi-file project using PlatformIO + Arduino Framework.\n"
        "Generate SensorManager, AlertManager, and DisplayManager modules.\n"
        "When the system is disabled:\n"
        "alarms should stop\n"
        "buzzer should stop\n"
        "monitoring values can still be displayed\n"
        "Use a serial monitor at 115200 baud and ensure the complete project compiles."
    )
    decision = AgentTurnRouter().route(request)
    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True


def test_direct_monitor_commands_still_route_to_hardware_controls() -> None:
    assert AgentTurnRouter().route("Please stop the serial monitor now.").intent is AgentTurnIntent.STOP_MONITOR
    assert AgentTurnRouter().route("Can you start the serial monitor?").intent is AgentTurnIntent.MONITOR


def test_turn_router_explicit_build_prohibition_overrides_edit_validation() -> None:
    decision = AgentTurnRouter().route("change the delay to 250ms but don't build it")

    assert decision.intent is AgentTurnIntent.EDIT
    assert decision.requires_write is True
    assert decision.requires_build is False
    assert "build" in decision.prohibited_actions


def test_turn_router_fix_it_uses_failed_context() -> None:
    decision = AgentTurnRouter().route("fix it", AgentTurnContext(last_run_status="failed", last_build_status="failed"))

    assert decision.intent is AgentTurnIntent.REPAIR
    assert decision.requires_build is True


def test_turn_router_confirm_requires_pending_flash_context() -> None:
    pending = AgentTurnRouter().route("yes", AgentTurnContext(active_run_status="awaiting_flash_confirmation"))
    idle = AgentTurnRouter().route("yes")

    assert pending.intent is AgentTurnIntent.CONFIRM
    assert pending.requires_confirmation is True
    assert idle.intent is AgentTurnIntent.UNKNOWN


def test_compound_build_a_project_request_routes_to_generation_and_build() -> None:
    decision = AgentTurnRouter().route(
        "Build a complete ESP32 smart room monitor project. "
        "When the ESP32 starts, initialize the sensors. Set every GPIO clearly and validate that it compiles."
    )

    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True
    assert decision.requested_actions == ("generate", "build")


def test_inspect_first_then_create_project_still_routes_to_generation() -> None:
    decision = AgentTurnRouter().route(
        "Inspect the current architecture first, then create a complete ESP32 temperature monitor PlatformIO project."
    )

    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True


def test_scoped_quality_rule_does_not_disable_multifile_generation() -> None:
    decision = AgentTurnRouter().route(
        "Create a complete ESP32 multi-file PlatformIO project with src/main.cpp "
        "and include/SystemManager.h. Do not create fake files just to make "
        "the project look multi-file. Check that it builds."
    )
    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True
    assert "write" not in decision.prohibited_actions


def test_blanket_file_prohibition_still_blocks_generation() -> None:
    decision = AgentTurnRouter().route("Create an ESP32 project, but do not create any files.")
    assert decision.intent is AgentTurnIntent.INSPECT
    assert decision.requires_write is False
    assert "write" in decision.prohibited_actions


def test_common_text_file_request_can_generate_without_platformio_build() -> None:
    decision = AgentTurnRouter().route(
        "Create docs/design.md, src/service.go, and a Dockerfile for this project."
    )
    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is False


def test_scoped_negative_instruction_alone_does_not_start_generation() -> None:
    decision = AgentTurnRouter().route("Do not create fake files just to make the project look multi-file.")
    assert decision.requires_write is False
    assert decision.starts_product_run is False


def test_failed_run_continue_reuses_last_explicit_project_request() -> None:
    request = "Create a complete ESP32 multi-file project. Do not create fake files."
    session = SimpleNamespace(messages=[
        SimpleNamespace(role="user", content=request),
        SimpleNamespace(role="assistant", content="The previous run failed."),
        SimpleNamespace(role="user", content="continue this"),
    ])
    resumed = _failed_run_continuation(session, SimpleNamespace(status="failed"), "continue this")
    assert resumed is not None
    assert AgentTurnRouter().route(resumed).intent is AgentTurnIntent.GENERATE
    assert _failed_run_continuation(session, SimpleNamespace(status="completed"), "continue this") is None


def test_inspect_and_propose_only_stays_read_only() -> None:
    decision = AgentTurnRouter().route("Inspect the current architecture first and propose the next useful feature.")

    assert decision.intent is AgentTurnIntent.INSPECT
    assert decision.starts_product_run is False


def test_composite_generate_and_simulate_keeps_write_intent() -> None:
    decision = AgentTurnRouter().route("create an ESP32 blink project and simulate it in Wokwi")
    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True
    assert "simulate" in decision.requested_actions


def test_composite_repair_and_simulate_keeps_repair_intent() -> None:
    context = AgentTurnContext(last_run_status="failed", last_build_status="failed")
    decision = AgentTurnRouter().route("fix the build error and simulate it in Wokwi", context)
    assert decision.intent is AgentTurnIntent.REPAIR
    assert decision.requires_write is True
    assert "simulate" in decision.requested_actions


def test_approved_plan_execution_is_explicit_write_build_capability() -> None:
    decision = approved_plan_execution_decision()
    assert decision.intent is AgentTurnIntent.EDIT
    assert decision.routing_source == "approved_plan"
    assert decision.requires_write is True
    assert decision.requires_build is True
    assert decision.requested_actions == ("execute_plan", "edit", "build")
