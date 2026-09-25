"""Capability profiles for the unified Forge agent runtime.

One model/tool loop serves every conversational mode. Profiles only change the
trusted tools and completion requirements exposed to that loop.
"""
from __future__ import annotations

from dataclasses import dataclass

from .tool_contracts import ToolName, WRITE_TOOLS
from .tool_policy import ToolPermissionPolicy


@dataclass(frozen=True, slots=True)
class AgentCapabilityProfile:
    profile_id: str
    label: str
    allowed_tools: frozenset[ToolName]
    require_build: bool = False
    allow_no_changes: bool = True
    allow_subagents: bool = False

    def policy(self, *, max_bytes_per_file: int = 128_000) -> ToolPermissionPolicy:
        decisions = {tool: "deny" for tool in ToolName}
        for tool in self.allowed_tools:
            decisions[tool] = "allow"
        return ToolPermissionPolicy(
            policy_id=f"forgex-capability-{self.profile_id}-v1",
            decisions=decisions,
            max_file_bytes=max(1, min(int(max_bytes_per_file), 512_000)),
            max_content_bytes=max(1, min(int(max_bytes_per_file), 512_000)),
        )


_BASE_READ = frozenset({
    ToolName.LIST_FILES,
    ToolName.GLOB_FILES,
    ToolName.GREP_SEARCH,
    ToolName.READ_FILE,
    ToolName.LOAD_SKILL,
    ToolName.MEMORY_SEARCH,
})

ASK_PROFILE = AgentCapabilityProfile(
    "ask", "Ask / inspect", _BASE_READ | {ToolName.SPAWN_SUBAGENT},
    allow_no_changes=True, allow_subagents=True,
)
PLAN_PROFILE = AgentCapabilityProfile(
    "plan", "Plan", _BASE_READ | {ToolName.UPDATE_PLAN, ToolName.SPAWN_SUBAGENT},
    allow_no_changes=True, allow_subagents=True,
)
EDIT_PROFILE = AgentCapabilityProfile(
    "edit", "Edit", _BASE_READ | WRITE_TOOLS | {ToolName.UPDATE_PLAN, ToolName.SPAWN_SUBAGENT},
    allow_no_changes=False, allow_subagents=True,
)
BUILD_ONLY_PROFILE = AgentCapabilityProfile(
    "build_only", "Build only", _BASE_READ | {ToolName.BUILD_FIRMWARE}, require_build=True, allow_no_changes=True,
)
BUILD_REPAIR_PROFILE = AgentCapabilityProfile(
    "build_repair", "Build and repair", _BASE_READ | WRITE_TOOLS | {
        ToolName.UPDATE_PLAN, ToolName.WRITE_FILE, ToolName.EDIT_FILE_SIMPLE, ToolName.APPLY_PATCH,
        ToolName.SPAWN_SUBAGENT, ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION,
    }, require_build=True, allow_no_changes=True, allow_subagents=True,
)
SIMULATE_PROFILE = AgentCapabilityProfile(
    "simulate", "Build and simulate", _BASE_READ | {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION},
    require_build=True, allow_no_changes=True,
)


def profile_for_autonomy(autonomy: str, *, requires_write: bool = False, requires_build: bool = False) -> AgentCapabilityProfile:
    normalized = (autonomy or "").casefold()
    if normalized == "read_only":
        return ASK_PROFILE
    if normalized == "plan_only":
        return PLAN_PROFILE
    if normalized == "staged_changes":
        return EDIT_PROFILE
    if normalized == "build_only_strict":
        return BUILD_ONLY_PROFILE
    if normalized == "simulate":
        return BUILD_REPAIR_PROFILE if requires_write else SIMULATE_PROFILE
    if normalized in {"build_only", "build_then_confirm_flash"}:
        return BUILD_REPAIR_PROFILE if requires_write else BUILD_ONLY_PROFILE
    if requires_build:
        return BUILD_REPAIR_PROFILE if requires_write else BUILD_ONLY_PROFILE
    return EDIT_PROFILE if requires_write else ASK_PROFILE
