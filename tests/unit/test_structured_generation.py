"""Controller policy, actual linker evidence and safe plan compatibility."""
import asyncio
import copy
import json

import pytest

from backend.agent_runtime.build_diagnostics import parse_build_diagnostics, linker_repair_focus
from backend.agent_runtime.generation_controller import GenerationController, validate_plan_steps
from backend.agent_runtime.observation_context import render_observations
from backend.agent_runtime.public_progress import LivePlan
from backend.agent_runtime.tool_contracts import ProductToolCall
from backend.services.project_profile import project_source_layout, inspect_project_profile


def contract():
    return {"revision": "task-1", "requirements": [{"id": "req-1", "text": "Implement display"}]}


def step(**extra):
    return {"key": "display", "text": "Implement display", "status": "in_progress",
            "verification": "edit", "paths": ["src/Display.cpp"], "requirement_ids": ["req-1"], **extra}


def test_plan_graph_round_trips_through_canonical_tool_and_rejects_unknown_requirement():
    steps = [step(), {"key": "integrate", "text": "Integrate", "status": "pending", "depends_on": ["display"]}]
    call = ProductToolCall.parse({"id": "p", "type": "update_plan", "arguments": {"steps": steps}})
    assert call.arguments["steps"] == steps
    with pytest.raises(ValueError, match="REQUIREMENT_UNKNOWN"):
        validate_plan_steps(steps, {"req-other"})


@pytest.mark.parametrize("mutation,reason", [
    ({"paths": ["../outside.cpp"]}, "PATH_UNSAFE"),
    ({"paths": ["C:/outside.cpp"]}, "PATH_UNSAFE"),
    ({"paths": [".secrets/config.h"]}, "PATH_UNSAFE"),
    ({"depends_on": ["missing"]}, "DEPENDENCY_UNKNOWN"),
    ({"depends_on": ["display"]}, "DEPENDENCY_CYCLE"),
    ({"status": "invented"}, "STEP_INVALID"),
])
def test_plan_rejects_unsafe_or_unexecutable_graphs(mutation, reason):
    with pytest.raises(ValueError, match=reason):
        validate_plan_steps([step(**mutation)])


def test_legacy_plan_still_works_without_new_metadata():
    legacy = [{"text": "Inspect", "status": "in_progress"}]
    assert validate_plan_steps(legacy) == tuple(legacy)


def test_saved_session_plan_preserves_milestone_graph_and_binds_metadata(tmp_path):
    from backend.agent_runtime.session_store import AgentSessionStore
    store = AgentSessionStore(tmp_path / "sessions.json")
    session = store.create(project_id="p", title="Monitor")
    steps = [step(), {"key": "integrate", "text": "Integrate", "status": "pending", "depends_on": ["display"]}]
    first = store.record_plan(session.session_id, run_id="r", steps=steps).current_plan
    restored = AgentSessionStore(store.path).get(session.session_id).current_plan
    assert list(restored.steps) == steps
    assert restored.plan_hash == first.plan_hash
    changed = copy.deepcopy(steps)
    changed[0]["paths"] = ["src/Other.cpp"]
    second = store.record_plan(session.session_id, run_id="r", steps=changed).current_plan
    assert second.plan_hash != first.plan_hash


def test_read_and_noop_cannot_reset_generation_stalls_but_changed_write_can():
    controller = GenerationController(contract())
    for _ in range(3):
        controller.observe({"tool": "read_file", "status": "completed", "output": {"path": "different.cpp"}})
        controller.observe({"tool": "write_file", "status": "completed", "output": {"path": "src/Display.cpp", "modified": False}})
        controller.settle()
    assert controller.stalled_turns == 3
    tools = ("read_file", "write_file", "spawn_subagent", "memory_search")
    assert controller.narrow_tools(tools) == ("read_file", "write_file")
    controller.observe({"tool": "write_file", "status": "completed", "output": {"path": "src/Display.cpp", "modified": True}})
    assert controller.snapshot()["stalled_turns"] == 0
    assert controller.snapshot()["dirty_paths"] == ["src/Display.cpp"]
    controller.settle()
    assert controller.stalled_turns == 0


def test_saved_controller_retains_guards_and_rejects_future_version():
    controller = GenerationController(contract())
    controller.stalled_turns = 4
    assert GenerationController(contract(), controller.snapshot()).stalled_turns == 4
    with pytest.raises(ValueError, match="CONTROLLER_VERSION_UNSUPPORTED"):
        GenerationController(contract(), {"version": 2})


def test_failed_exact_match_reorients_to_other_authorized_edits_without_resetting_guards():
    controller = GenerationController(contract())
    args = {"contract": contract(), "plan": [], "files": {"src/Display.cpp": "one"}, "source_revision": "r1"}
    controller.focus(**args)
    controller.stalled_turns = 2
    controller.observe({"tool": "edit_file_simple", "status": "failed", "output": {
        "error": "edit_match_must_be_exactly_one", "path": "src/Display.cpp",
        "current_excerpt": {"truncated": False, "content": "void begin();"}}})
    controller.settle()
    restored = GenerationController(contract(), controller.snapshot())
    assert restored.stalled_turns == 3
    focus = restored.focus(**args)
    assert "current_excerpt" in focus["next_action"]
    allowed = ("read_file", "edit_file_simple", "write_file", "apply_patch", "build_firmware", "flash_firmware")
    assert restored.narrow_tools(allowed) == allowed[:-1]
    assert focus["edit_recovery"]["required_action"] == "rewrite_file"
    restored.settle()
    restored.settle()
    assert restored.stalled_turns == 5  # No budget or no-progress guard was reset.
    restored.focus(**{**args, "source_revision": "r2", "files": {"src/Display.cpp": "two"}})
    assert not restored.failed_edit
    assert "read_file" in restored.narrow_tools(allowed)


def test_completion_claim_needs_files_and_host_evidence():
    controller = GenerationController(contract())
    focus = controller.focus(contract=contract(), plan=[step(status="completed")],
        files={"src/Display.cpp": "hash"}, source_revision="r1")
    assert focus["active_milestone"]["key"] == "display"


def test_changed_source_cannot_reuse_completed_milestone_evidence():
    controller = GenerationController(contract())
    plan = [step(status="completed", evidence=["actual-write-1"])]
    args = {"contract": contract(), "plan": plan, "files": {"src/Display.cpp": "one"}, "source_revision": "r1"}
    assert controller.focus(**args)["active_milestone"] is None
    args.update(files={"src/Display.cpp": "two"}, source_revision="r2")
    assert controller.focus(**args)["active_milestone"]["key"] == "display"
    restored = GenerationController(contract(), controller.snapshot())
    assert restored.focus(**args)["active_milestone"]["key"] == "display"
    args["plan"] = [step(status="completed", evidence=["actual-write-1", "actual-write-2"])]
    assert restored.focus(**args)["active_milestone"] is None


def test_live_plan_preserves_step_key_and_reopens_dependent_work():
    live = LivePlan()
    live.update([step()])
    live.observe("write-1", True, "write_file")
    live.update([step(status="completed"), {"key": "integrate", "text": "Integrate", "status": "in_progress", "verification": "edit", "depends_on": ["display"]}])
    live.observe("write-2", True, "write_file")
    live.update([step(status="completed"), {"key": "integrate", "text": "Integrate", "status": "completed", "verification": "edit", "depends_on": ["display"]}])
    identity = live.steps[0]["id"]
    live.invalidate(["src/Display.cpp"])
    assert all(item["status"] == "pending" for item in live.steps)
    live.update([step(text="Implement current display", status="completed")])
    assert live.steps[0]["id"] == identity and live.steps[0]["status"] != "completed"


def test_structured_milestone_build_waits_for_coherent_files():
    controller = GenerationController(contract())
    controller.observe({"tool": "write_file", "status": "completed", "output": {"path": "src/Display.cpp", "created": True}})
    controller.active_key = "display"
    assert not controller.should_build([step()], ["src/Display.cpp"], 700)
    assert not controller.should_build([step(status="completed")], [], 700)
    assert controller.should_build([step(status="completed")], ["src/Display.cpp"], 700)


def test_actual_gnu_linker_formats_are_structured_without_source_line(tmp_path):
    text = ("c:/toolchain/bin/ld.exe: .pio\\build\\esp32dev\\src\\main.cpp.o:(.literal._Z5setupv+0x2c): undefined reference to `DisplayManager::init()'\n"
            "c:/toolchain/bin/ld.exe: .pio\\build\\esp32dev\\src\\main.cpp.o:(.text+0x2): undefined reference to `DisplayManager::init()'\n"
            "ld: src/duplicate.cpp:4: multiple definition of `Alarm::update()'; src/original.cpp:4: first defined here\n")
    compiler, linker = parse_build_diagnostics(text, tmp_path)
    assert compiler == [] and len(linker) == 2
    assert linker[0]["symbol"] == "DisplayManager::init()"
    assert linker[0]["path"] == "src/main.cpp" and linker[0]["line"] is None
    assert linker[1]["symbol"] == "Alarm::update()"


def test_compiler_paths_are_contained_and_early_errors_survive_large_logs(tmp_path):
    error = str(tmp_path / "src/main.cpp") + ":12:3: error: bad call\n"
    compiler, _ = parse_build_diagnostics(error + "normal output\n" * 5000 + "C:/outside/file.cpp:2: error: outside\n", tmp_path)
    assert compiler[0]["path"] == "src/main.cpp"
    assert compiler[1]["path"] is None


def test_linker_context_survives_compaction_and_does_not_invent_an_edit_target(tmp_path):
    files = {"include/ui.h": "class DisplayManager { public: void init(); };\n" + "// long\n" * 100,
             "src/ui.cpp": '#include "ui.h"\n', "src/main.cpp": "DisplayManager display;\n"}
    _, diagnostics = parse_build_diagnostics("ld: undefined reference to `DisplayManager::init()'", tmp_path)
    build = {"build_id": "b", "workspace_revision": "r", "linker_diagnostics": diagnostics}
    focus = linker_repair_focus(build, files, files.__getitem__)
    original = copy.deepcopy(focus)
    rendered = render_observations([{"tool": "linker_repair_focus", "output": focus}], max_chars=2800)
    assert "DisplayManager::init()" in rendered and "instruction" in rendered
    assert focus == original and focus["candidate_matches_are_advisory"]
    unknown = linker_repair_focus({**build, "linker_diagnostics": [{"symbol": "unknown()", "message": "unresolved"}]}, files, files.__getitem__)
    assert unknown["current_sources"] == []


def test_project_profile_and_host_milestone_use_custom_source_layout(tmp_path):
    (tmp_path / "platformio.ini").write_text("[platformio]\nsrc_dir=firmware\ninclude_dir=headers\n[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    (tmp_path / "firmware").mkdir()
    (tmp_path / "firmware/main.cc").write_text("int sample;\n")
    layout = project_source_layout(tmp_path)
    assert layout == {"src_dir": "firmware", "include_dir": "headers"}
    assert inspect_project_profile(tmp_path).source_directory == "firmware"
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
    from tests.unit.test_agent_verification_freshness import Builder, progress
    class Planner:
        supports_durable_continuation = True
    builder = Builder()
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=Planner(), platformio=builder,
        progress=progress, trusted_project=True, policy=BUILD_REPAIR_PROFILE.policy())
    assert asyncio.run(loop._verify_host_milestone(None, call_id="custom-build"))
    assert builder.calls == 1
