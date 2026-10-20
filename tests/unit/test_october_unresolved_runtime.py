"""Remaining October incidents: exercise provider-visible requests, not UI copy."""
import asyncio
import json

import pytest

from backend.agent_runtime.api_planner_provider import _planner_instruction
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.models import ModelResponse
from tests.unit.test_native_agent_tools import Router


def response(name="read", *, call_id="read-1", arguments=None):
    return ModelResponse("", "nvidia_nim", "selected-model", tool_calls=(
        {"id": call_id, "name": name, "arguments": arguments or {"path": "platformio.ini"}},))


def test_native_followup_receives_current_host_repair_context():
    router = Router([response(), response(call_id="read-2"), response(call_id="read-3")], context_window=8192)
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="selected-model")

    async def scenario():
        await planner.request_turn_async(task="Build the original request", tool_results=(
            {"tool": "build_repair_focus", "status": "completed", "output": {"current_blocker": "dependency-1"}},),
            allowed_tools=("read_file",), run_id="run", turn=1, stream=False)
        for turn in (2, 3):
            await planner.request_turn_async(task="Build the original request", tool_results=(
                {"call_id": f"read-{turn - 1}", "tool": "read_file", "status": "completed", "output": "ini"},
                {"tool": "build_repair_focus", "status": "completed", "output": {"current_blocker": f"dependency-{turn}"}},
                {"tool": "interface_repair_focus", "status": "completed", "output": {"declarations": [f"signature-{turn}"]}},
            ), allowed_tools=("read_file",), run_id="run", turn=turn, stream=False)

    asyncio.run(scenario())
    second = json.dumps(router.requests[1].metadata["forgex_messages"])
    third = json.dumps(router.requests[2].metadata["forgex_messages"])
    assert "dependency-2" in second and "signature-2" in second
    assert "dependency-1" not in second
    assert "dependency-3" in third and "signature-3" in third
    assert "dependency-2" not in third and "signature-2" not in third
    # Host guidance is user-role data, never a fabricated tool response.
    messages = router.requests[2].metadata["forgex_messages"]
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == ["read-1", "read-2"]


def test_unknown_native_tool_repair_lists_actual_advertised_functions():
    router = Router([response("invented_private_function"),
        response("write", call_id="fixed", arguments={"path": "include/Module.h", "content": "#pragma once"})])
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="selected-model")
    result = asyncio.run(planner.request_turn_async(task="Create firmware", tool_results=(),
        allowed_tools=("write_file", "read_file"), run_id="run", turn=1, stream=False))
    assert result.tool_calls[0].tool_type == "write_file" and len(router.requests) == 2
    repair = router.requests[1]
    names = [item["function"]["name"] for item in repair.metadata["forgex_native_tools"]]
    assert "Use only these allowed functions: " + ", ".join(names) in repair.system_prompt
    assert "invented_private_function" not in repair.system_prompt
    assert all(request.model_id == "selected-model" and not request.allow_fallback for request in router.requests)


@pytest.mark.parametrize("smoke", [False, True])
def test_native_instructions_use_wire_names(smoke):
    prompt = _planner_instruction(native=True, smoke=smoke, allowed_tools=("write_file", "read_file"))
    assert "write_file" not in prompt and "read_file" not in prompt
    assert "write" in prompt
    compatibility = _planner_instruction(native=False, smoke=smoke, allowed_tools=("write_file",))
    assert "write_file" in compatibility


@pytest.mark.parametrize("caller,message", [
    ("src/main.cpp", "'class SensorManager' has no member named 'getLightPercent'"),
    ("src/SensorManager.cpp", "'lightPercent' was not declared in this scope"),
])
def test_compiler_focus_survives_compaction_with_current_callers_and_headers(tmp_path, caller, message):
    from tests.unit.test_live_session_recovery import loop_for
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header = tmp_path / "include/SensorManager.h"
    header.write_text("class SensorManager { public: void update(); float getLightPercentage(); };\n")
    (tmp_path / caller).write_text("void loop() { sensorManager.getLightPercent(); }\n")
    loop = loop_for(tmp_path, object())
    loop.last_build = {"success": False, "workspace_revision": "old", "compiler_diagnostics": [
        {"path": caller, "line": 1, "severity": "error", "message": message}]}
    loop.observations = [{"call_id": f"old-{i}", "tool": "read_file", "status": "completed", "output": "old"}
        for i in range(35)]
    context, checkpoint = loop._compacted_observations()
    focus = next(item["output"] for item in context if item["tool"] == "compiler_repair_focus")
    assert checkpoint is not None
    assert {s["path"] for s in focus["current_sources"]} == {caller, "include/SensorManager.h"}
    assert "getLightPercentage" in json.dumps(focus)
    previous_hash = next(s["content_sha256"] for s in focus["current_sources"] if s["path"] == "include/SensorManager.h")
    header.write_text("class SensorManager { public: float getLightPercent(); };\n")
    loop.last_build["verification_stale"] = True
    context, _ = loop._compacted_observations()
    refreshed = next(item["output"] for item in context if item["tool"] == "compiler_repair_focus")
    assert refreshed["diagnostics_stale"]
    assert next(s["content_sha256"] for s in refreshed["current_sources"] if s["path"] == "include/SensorManager.h") != previous_hash
    assert not loop.build_passed


def test_compiler_focus_never_reads_diagnostic_paths_outside_inventory():
    from backend.agent_runtime.build_recovery import compiler_repair_focus
    def forbidden(_path):
        raise AssertionError("unsafe diagnostic path must not be read")
    build = {"success": False, "compiler_diagnostics": [
        {"severity": "error", "path": "../secret.cpp", "line": 1, "message": "outside"},
        {"severity": "error", "path": "C:/secret.cpp", "line": 1, "message": "outside"},
        {"severity": "warning", "path": "src/main.cpp", "line": 1, "message": "warning"}]}
    assert compiler_repair_focus(build, ["src/main.cpp", "../secret.cpp", "C:/secret.cpp"], forbidden) is None


def test_compiler_focus_pins_shared_type_definitions_and_private_fields_after_compaction(tmp_path):
    from tests.unit.test_live_session_recovery import loop_for
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "include/SystemState.h").write_text("// shared state\n" * 35
        + "struct SystemStates {\n  int systemState;\n  bool sensorError;\n};\n")
    (tmp_path / "include/AlarmManager.h").write_text("enum class BuzzerPatternType { OFF, SECURITY };\n"
        + "class AlarmManager {\npublic:\n  void update();\n" + "// public API\n" * 22
        + "private:\n  BuzzerPatternType currentPattern;\n};\n")
    (tmp_path / "src/AlarmManager.cpp").write_text("#include \"AlarmManager.h\"\nvoid AlarmManager::update() {}\n")
    loop = loop_for(tmp_path, object())
    loop.last_build = {"success": False, "compiler_diagnostics": [
        {"severity": "error", "path": "src/AlarmManager.cpp", "line": 2,
         "message": "'const struct SystemStates' has no member named 'system'; did you mean 'systemState'?"},
        {"severity": "error", "path": "src/AlarmManager.cpp", "line": 2,
         "message": "'PATTERN_OFF' is not a member of 'BuzzerPatternType'"}]}
    loop.observations = [{"call_id": f"old-{i}", "tool": "read_file", "status": "completed", "output": "old"}
        for i in range(35)]
    context, checkpoint = loop._compacted_observations()
    focus = next(x["output"] for x in context if x["tool"] == "compiler_repair_focus")
    sources = {x["path"]: x for x in focus["current_sources"]}
    assert checkpoint is not None and "include/SystemState.h" in sources
    assert "int systemState;" in sources["include/SystemState.h"]["numbered_preview"]
    assert "BuzzerPatternType currentPattern;" in sources["include/AlarmManager.h"]["numbered_preview"]
    assert "enum class BuzzerPatternType { OFF, SECURITY };" in sources["include/AlarmManager.h"]["numbered_preview"]
    assert all(x["preview_only"] for x in sources.values()) and not loop.build_passed


def test_type_declaration_hints_cannot_authorize_reads_outside_the_source_inventory():
    from backend.agent_runtime.build_recovery import compiler_repair_focus
    reads = []
    def read(path):
        reads.append(path)
        assert path == "src/main.cpp"
        return "void loop() {}\n"
    focus = compiler_repair_focus({"success": False, "compiler_diagnostics": [
        {"severity": "error", "path": "src/main.cpp", "line": 1,
         "message": "'class PrivateType' has no member named 'missing'"}]},
        ["src/main.cpp", "../secret.h", "C:/secret.h"], read,
        declarations=[{"path": path, "line": 1, "declaration": "class PrivateType {"}
            for path in ("../secret.h", "C:/secret.h", "include/unlisted.h")])
    assert reads == ["src/main.cpp"] and len(focus["current_sources"]) == 1


def test_unadvertised_shell_is_not_misreported_as_a_schema_error():
    from backend.agent_runtime.api_planner_provider import ApiPlannerError
    router = Router(response("bash", arguments={"command": "private-command-value"}))
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="selected-model")
    with pytest.raises(ApiPlannerError) as caught:
        asyncio.run(planner.request_turn_async(task="Inspect", tool_results=(),
            allowed_tools=("read_file",), run_id="run", turn=1, stream=False))
    assert caught.value.safe_code == "provider_requested_forbidden_tool"
    assert caught.value.details["parse_reason"] == "provider_tool_not_allowed"
    assert "expected_schema" not in caught.value.details
    assert "missing_required" not in caught.value.details
    assert "private-command-value" not in json.dumps(caught.value.details)
    assert len(router.requests) == 2 and planner._native_messages == []


def test_planner_does_not_recommend_an_unimplemented_dependency_function():
    for native in (False, True):
        prompt = _planner_instruction(native=native, allowed_tools=("resolve_dependency",))
        assert "resolve_dependency" in prompt and "search_libraries" not in prompt


def test_configuration_repair_exposes_only_phase_tools_and_restores_profile(tmp_path):
    from tests.unit.test_live_session_recovery import loop_for, INVALID_INI, VALID_INI, dependency_failure
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    class ValidationBuilder(Builder):
        async def validate_dependencies(self, root, **kwargs):
            assert (root / "platformio.ini").read_text() == VALID_INI
            return {"valid": True}
    allowed = []
    planner = SequencePlanner([plan(path="platformio.ini", content=VALID_INI, call_id="fix-config"),
        plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n", call_id="write-main")],
        callback=lambda kwargs: allowed.append(set(kwargs["allowed_tools"])))
    loop = loop_for(tmp_path, planner, ValidationBuilder(), max_turns=2, max_progress_extension_turns=0,
        resume_checkpoint={"observations": [{"tool": "build_firmware", "status": "completed", "output": dependency_failure()}]})
    asyncio.run(loop.run(task="Continue original project", require_build=True))
    assert {"read_file", "write_file", "resolve_dependency", "build_firmware"} <= allowed[0]
    assert "verify_requirements" not in allowed[0] and "spawn_subagent" not in allowed[0]
    assert "verify_requirements" in allowed[1] and "spawn_subagent" in allowed[1]
    assert (tmp_path / "src/main.cpp").exists()


def test_policy_blocked_configuration_routes_to_repair_without_weakening_the_guard(tmp_path):
    from backend.security.build_policy import validate_build_configuration
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_live_session_recovery import loop_for, VALID_INI
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    bad_ini = VALID_INI + "build_flags = -D DEBUG_MODE=0 -Iinclude\n"
    (tmp_path / "platformio.ini").write_text(bad_ini)
    class GuardedBuilder(Builder):
        def inspect_configuration(self, root, **kwargs):
            return PlatformIOService.inspect_configuration(self, root, **kwargs)
        async def validate_dependencies(self, root, **kwargs):
            _, parser, _ = self.inspect_configuration(root, **kwargs)
            validate_build_configuration(parser)
            return {"valid": True}
    allowed = []
    builder = GuardedBuilder()
    planner = SequencePlanner([plan(path="src/main.cpp", content="must not write yet\n", call_id="too-early"),
        plan(path="platformio.ini", content=VALID_INI, call_id="fix-config"),
        plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n", call_id="after-config")],
        callback=lambda kwargs: allowed.append(set(kwargs["allowed_tools"])))
    loop = loop_for(tmp_path, planner, builder, max_turns=3, max_progress_extension_turns=0)
    asyncio.run(loop.run(task="Generate and compile the project", require_build=True))
    assert "verify_requirements" not in allowed[0] and "spawn_subagent" not in allowed[0]
    assert any(x.get("error_code") == "BUILD_CONFIGURATION_REPAIR_REQUIRED" for x in loop.observations)
    assert "verify_requirements" in allowed[2] and "spawn_subagent" in allowed[2]
    assert (tmp_path / "src/main.cpp").read_text() == "void setup() {}\nvoid loop() {}\n"
    assert builder.calls == 0
    (tmp_path / "platformio.ini").write_text(bad_ini)
    with pytest.raises(ValueError, match="BUILD_CONFIGURATION_REQUIRES_TRUST"):
        asyncio.run(builder.validate_dependencies(tmp_path))


def failed_compiler_checkpoint(root):
    from backend.agent_runtime.autonomous_loop import _snapshot_for_context
    return {"observations": [{"call_id": "failed-build", "tool": "build_firmware", "status": "completed",
        "output": {"success": False, "workspace_revision": _snapshot_for_context(root).revision,
            "compiler_diagnostics": [{"path": "src/main.cpp", "line": 1, "severity": "error",
                "message": "'class SensorManager' has no member named 'missing'"}]}}]}


def test_text_without_tool_gets_a_bounded_chance_to_act_on_saved_compiler_errors(tmp_path):
    from tests.unit.test_live_session_recovery import loop_for
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    from backend.agent_runtime.tool_contracts import ProductToolPlan
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("void loop() { sensor.missing(); }\n")
    (tmp_path / "include").mkdir()
    (tmp_path / "include/SensorManager.h").write_text("class SensorManager { public: void update(); };\n")
    builder = Builder()
    read = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Inspect current caller",
        "final": False, "tool_calls": [{"id": "inspect", "type": "read_file",
            "arguments": {"path": "src/main.cpp"}}]})
    read_header = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Inspect current declaration",
        "final": False, "tool_calls": [{"id": "inspect-header", "type": "read_file",
            "arguments": {"path": "include/SensorManager.h"}}]})
    fixed = "void setup() {}\nvoid loop() {}\n"
    planner = SequencePlanner([plan(final=True), read, plan(final=True), read_header, plan(final=True),
        plan(path="src/main.cpp", content=fixed), plan(final=True)])
    loop = loop_for(tmp_path, planner, builder, max_turns=7, max_progress_extension_turns=0,
        resume_checkpoint=failed_compiler_checkpoint(tmp_path))
    result = asyncio.run(loop.run(task="Repair the saved compiler failure", require_build=True))
    assert builder.calls == 1 and result.build_verified
    assert len(planner.seen_results) == 7
    assert (tmp_path / "src/main.cpp").read_text() == fixed
    correction = next(x for x in planner.seen_results[1] if x["tool"] == "build_completion_validation")
    assert "No tool call" in correction["output"]["message"]
    assert any(x["tool"] == "read_file" for x in loop.observations)


@pytest.mark.parametrize("previous_reminders", [0, 2])
def test_repeated_text_without_actions_stops_without_recompiling_failed_source(tmp_path, previous_reminders):
    from tests.unit.test_live_session_recovery import loop_for
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("void loop() { sensor.missing(); }\n")
    builder = Builder()
    planner = SequencePlanner([plan(final=True)] * 8)
    checkpoint = {**failed_compiler_checkpoint(tmp_path), "build_completion_reminders": previous_reminders}
    loop = loop_for(tmp_path, planner, builder, max_turns=8, max_progress_extension_turns=0,
        resume_checkpoint=checkpoint)
    result = asyncio.run(loop.run(task="Repair the saved compiler failure", require_build=True))
    assert len(planner.seen_results) == 3 - previous_reminders and builder.calls == 0
    assert result.classification == "BUILD_ERROR" and not result.build_verified


def test_text_completion_still_builds_when_failed_diagnostics_are_from_older_source(tmp_path):
    from tests.unit.test_live_session_recovery import loop_for
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/main.cpp"
    source.write_text("void loop() { sensor.missing(); }\n")
    checkpoint = failed_compiler_checkpoint(tmp_path)
    source.write_text("void setup() {}\nvoid loop() {}\n")
    builder = Builder()
    planner = SequencePlanner([plan(final=True)])
    loop = loop_for(tmp_path, planner, builder, max_turns=1, max_progress_extension_turns=0,
        resume_checkpoint=checkpoint)
    result = asyncio.run(loop.run(task="Verify the changed draft", require_build=True))
    assert builder.calls == 1 and result.build_verified


@pytest.mark.parametrize("model,endpoint,turn,required", [
    ("nvidia/nemotron-3-super-120b-a12b", "https://integrate.api.nvidia.com/v1", 2, True),
    ("nvidia/nemotron-3-super-120b-a12b", "https://integrate.api.nvidia.com/v1", 3, False),
    ("other-model", "https://integrate.api.nvidia.com/v1", 2, False),
    ("nvidia/nemotron-3-super-120b-a12b", "https://custom.example/v1", 2, False),
])
def test_required_tool_choice_is_scoped_to_the_verified_host_and_immediate_correction(model, endpoint, turn, required):
    router = Router(response())
    router.registry.base_url = lambda _provider: endpoint
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id=model)
    asyncio.run(planner.request_turn_async(task="Repair compiler errors", tool_results=(
        {"tool": "build_completion_validation", "status": "failed", "output": {
            "next_turn": 2, "require_tool_call": True}},),
        allowed_tools=("read_file",), run_id="run", turn=turn, stream=False))
    request = router.requests[0]
    assert (request.metadata.get("forgex_tool_choice") == "required") == required
    assert [item["function"]["name"] for item in request.metadata["forgex_native_tools"]] == ["read"]
    assert request.model_id == model and not request.allow_fallback
