"""Deterministic Forge Agent acceptance benchmarks.

These tests intentionally measure architecture/safety invariants without a paid model.
Real-model quality is covered by scripts/forge_agent_provider_smoke.py and manual task runs.
"""

from backend.agent_runtime.capability_profiles import (
    ASK_PROFILE,
    BUILD_ONLY_PROFILE,
    BUILD_REPAIR_PROFILE,
    EDIT_PROFILE,
    PLAN_PROFILE,
    SIMULATE_PROFILE,
)
from backend.agent_runtime.tool_contracts import ToolName
from backend.agent_runtime.turn_router import AgentTurnContext, AgentTurnIntent, AgentTurnRouter


ROUTER = AgentTurnRouter()


def test_acceptance_explanation_is_read_only_model_work() -> None:
    decision = ROUTER.route("Explain what src/main.cpp is doing and find any GPIO issues")
    assert decision.intent is AgentTurnIntent.INSPECT
    assert decision.requires_write is False
    assert ToolName.READ_FILE in ASK_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in ASK_PROFILE.allowed_tools


def test_acceptance_plan_cannot_mutate_or_build() -> None:
    assert ToolName.UPDATE_PLAN in PLAN_PROFILE.allowed_tools
    assert ToolName.READ_FILE in PLAN_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in PLAN_PROFILE.allowed_tools
    assert ToolName.BUILD_FIRMWARE not in PLAN_PROFILE.allowed_tools


def test_acceptance_plain_build_cannot_repair_by_editing() -> None:
    decision = ROUTER.route("Build this PlatformIO project")
    assert decision.intent is AgentTurnIntent.BUILD
    assert decision.requires_build is True
    assert decision.requires_write is False
    assert ToolName.BUILD_FIRMWARE in BUILD_ONLY_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in BUILD_ONLY_PROFILE.allowed_tools
    assert ToolName.APPLY_PATCH not in BUILD_ONLY_PROFILE.allowed_tools


def test_acceptance_natural_build_project_request_generates() -> None:
    decision = ROUTER.route(
        "so i need u to build a project on temperature detection which is based on esp32"
    )
    assert decision.intent is AgentTurnIntent.GENERATE
    assert decision.requires_write is True
    assert decision.requires_build is True


def test_acceptance_explicit_repair_can_edit_and_rebuild() -> None:
    decision = ROUTER.route(
        "Fix the compiler errors and rebuild the firmware",
        AgentTurnContext(last_build_status="failed"),
    )
    assert decision.intent is AgentTurnIntent.REPAIR
    assert decision.requires_write is True
    assert decision.requires_build is True
    assert ToolName.APPLY_PATCH in BUILD_REPAIR_PROFILE.allowed_tools
    assert ToolName.BUILD_FIRMWARE in BUILD_REPAIR_PROFILE.allowed_tools


def test_acceptance_simulation_has_no_write_or_physical_flash_tool() -> None:
    decision = ROUTER.route("Simulate the current firmware in Wokwi")
    assert decision.intent is AgentTurnIntent.SIMULATE
    assert decision.requires_build is True
    assert ToolName.RUN_SIMULATION in SIMULATE_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in SIMULATE_PROFILE.allowed_tools
    assert ToolName.RUN_COMMAND not in SIMULATE_PROFILE.allowed_tools


def test_acceptance_edit_uses_staged_precise_patch_without_shell() -> None:
    assert ToolName.APPLY_PATCH in EDIT_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE in EDIT_PROFILE.allowed_tools
    assert ToolName.RUN_COMMAND not in EDIT_PROFILE.allowed_tools


def test_acceptance_flash_is_separate_hardware_confirmation() -> None:
    decision = ROUTER.route("Flash the verified firmware to my ESP32")
    assert decision.intent is AgentTurnIntent.FLASH
    assert decision.requires_hardware is True
    assert decision.requires_confirmation is True
    assert decision.starts_product_run is False


def test_acceptance_user_can_forbid_writes_during_investigation() -> None:
    decision = ROUTER.route("Review the latest failure, but do not edit or change any files")
    assert decision.intent is AgentTurnIntent.INSPECT
    assert decision.requires_write is False
    assert "write" in decision.prohibited_actions
