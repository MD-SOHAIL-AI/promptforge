from __future__ import annotations

import pytest

from backend.agent_runtime.turn_router import AgentTurnContext, AgentTurnIntent, AgentTurnRouter


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
        ("diagnose the current build error", AgentTurnIntent.INSPECT, False),
        ("change delay to 250ms", AgentTurnIntent.EDIT, True),
        ("add Wi-Fi support", AgentTurnIntent.EDIT, True),
        ("create an ESP32 blink project", AgentTurnIntent.GENERATE, True),
        ("build it", AgentTurnIntent.BUILD, True),
        ("compile this project", AgentTurnIntent.BUILD, True),
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
