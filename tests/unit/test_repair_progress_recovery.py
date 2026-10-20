"""Repair progress must survive later stalls and interrupted tool boundaries."""
import asyncio
import json

import pytest

from backend.agent_runtime.tool_contracts import ProductToolPlan
from tests.unit.test_live_session_recovery import loop_for
from tests.unit.test_native_agent_tools import Router
from tests.unit.test_october_unresolved_runtime import response
from tests.unit.test_tool_runtime import SequencePlanner, plan


def update(index):
    return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Plan next repair",
        "final": False, "tool_calls": [{"id": f"plan-{index}", "type": "update_plan",
            "arguments": {"steps": [{"text": f"Repair step {index}", "status": "in_progress"}]}}]})


def test_new_workspace_progress_starts_a_new_bounded_reorientation(tmp_path):
    planner = SequencePlanner([plan(path="first.cpp", content="int first;", call_id="first"),
        update(1), update(2), update(3),
        plan(path="repaired.cpp", content="int repaired;", call_id="repair"), plan(final=True)])
    loop = loop_for(tmp_path, planner, max_turns=6, max_progress_extension_turns=0,
        resume_checkpoint={"turn": 3, "stalled_turns": 3, "reorientation_started_at": 1})
    result = asyncio.run(loop.run(task="Repair the saved source", require_build=False))
    assert result.classification != "AGENT_RUNTIME_NO_PROGRESS"
    assert (tmp_path / "repaired.cpp").read_text() == "int repaired;"
    assert loop._stalled_turns == 0 and loop._reorientation_started_at is None


def test_progress_does_not_disable_the_next_no_progress_guard(tmp_path):
    planner = SequencePlanner([plan(path="first.cpp", content="int first;"),
        *(update(index) for index in range(5)), plan(path="never.cpp", content="int never;")])
    loop = loop_for(tmp_path, planner, max_turns=10, max_progress_extension_turns=0,
        resume_checkpoint={"turn": 3, "stalled_turns": 3, "reorientation_started_at": 1})
    result = asyncio.run(loop.run(task="Repair the saved source", require_build=False))
    assert result.classification == "AGENT_RUNTIME_NO_PROGRESS"
    assert not (tmp_path / "never.cpp").exists()
    assert len(planner.seen_results) == 6  # One write, then the existing five-turn bound.


@pytest.mark.parametrize("changed", [False, True])
def test_write_checkpoint_updates_recovery_only_when_source_changes(tmp_path, changed):
    checkpoints = []
    async def capture(event, payload):
        if event == "tool.checkpoint":
            checkpoints.append(payload["_resume_checkpoint"])
    (tmp_path / "repaired.cpp").write_text("int original;")
    planner = SequencePlanner([plan(path="repaired.cpp", content="int repaired;" if changed else "int original;"), plan(final=True)])
    loop = loop_for(tmp_path, planner, max_turns=2, max_progress_extension_turns=0,
        resume_checkpoint={"turn": 3, "stalled_turns": 4, "reorientation_started_at": 1,
            "build_completion_reminders": 2, "tool_calls_used": 7, "elapsed_seconds": 10,
            "repair_without_build_turns": 5, "repair_groups": {"initial": 1}})
    loop._repair_groups = {"current": 3}
    loop._seen_build_pairs = {"current-source:current-diagnostic"}
    loop.repair_history = [{"strategy": "model_edit", "changed_paths": ["repaired.cpp"]}]
    loop.progress = capture
    asyncio.run(loop.run(task="Repair source", require_build=False))
    saved = checkpoints[0]
    assert saved["stalled_turns"] == (0 if changed else 4)
    assert saved["reorientation_started_at"] == (None if changed else 1)
    assert saved["build_completion_reminders"] == (0 if changed else 2)
    assert saved["repair_without_build_turns"] == (0 if changed else 5)
    assert saved["repair_groups"] == {"current": 3}
    assert saved["seen_build_pairs"] == ["current-source:current-diagnostic"]
    assert saved["repair_history"] == loop.repair_history
    assert saved["tool_calls_used"] == 8 and saved["elapsed_seconds"] >= 10


def test_large_compiler_focus_retains_structured_evidence_in_native_request():
    from backend.agent_runtime.router_planner import RouterProductPlanner
    # Six real-sized previews and eight diagnostics can exceed the host context
    # allocation once JSON escaping and source metadata have been included.
    focus = {"build_id": "failed-build", "diagnostics_stale": False,
        "diagnostics": [{"path": "src/main.cpp", "line": i + 1,
            "message": "'class SensorManager' has no member named 'missing' " + "x" * 340}
            for i in range(8)],
        "current_sources": [{"path": f"include/Module{i}.h", "content_sha256": str(i) * 64,
            "numbered_preview": "1: class Module { public: void update(); };\n" * 25,
            "preview_only": True} for i in range(6)],
        "instruction": "Read affected files, align their interfaces, then rebuild."}
    router = Router(response())
    planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id="selected-model")
    asyncio.run(planner.request_turn_async(task="Repair the compiler errors", tool_results=(
        {"tool": "compiler_repair_focus", "status": "completed", "output": focus},),
        allowed_tools=("read_file",), run_id="run", turn=1, stream=False))
    host = router.requests[0].metadata["forgex_messages"][-1]["content"]
    records = json.loads(host[host.index("["):])
    compiler = next(item for item in records if item["tool"] == "compiler_repair_focus")
    assert not compiler.get("output_truncated"), "Compiler evidence must not become a sliced JSON preview"
    assert compiler["output"]["diagnostics"] and compiler["output"]["current_sources"]
    assert "Read affected files" in compiler["output"]["instruction"]


@pytest.mark.parametrize("max_chars", [2800, 12000, 48000])
def test_bounded_compiler_context_keeps_errors_and_does_not_mutate_saved_evidence(max_chars):
    from backend.agent_runtime.observation_context import render_observations
    focus = {"tool": "compiler_repair_focus", "status": "completed", "output": {
        "diagnostics_stale": True,
        "diagnostics": [{"path": "src/main.cpp", "line": 3, "message": "missing declared member"}],
        "current_sources": [{"path": f"include/Module{i}.h", "content_sha256": "a" * 64,
            "numbered_preview": ('1: // Unicode source \u2014 \u4e16\u754c \\"' + "x" * 150 + "\n") * 10,
            "preview_only": True} for i in range(6)],
        "instruction": "Read complete affected files before editing; compile to verify."}}
    before = json.dumps(focus)
    rendered = render_observations((focus,), max_chars=max_chars)
    output = json.loads(rendered)[0]["output"]
    assert len(rendered) <= max_chars and json.dumps(focus) == before
    assert output["diagnostics_stale"] and output["diagnostics"][0]["message"] == "missing declared member"
    assert output["instruction"].startswith("Read complete")
    assert all(source["content_sha256"] == "a" * 64 for source in output["current_sources"])


def test_missing_source_directory_is_an_actionable_generation_blocker(tmp_path):
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_live_session_recovery import VALID_INI
    from tests.unit.test_platformio_service import FakeSubprocessManager
    manager = FakeSubprocessManager()
    service = PlatformIOService(manager)
    (tmp_path / "platformio.ini").write_text(VALID_INI)
    loop = loop_for(tmp_path, object(), service)
    result = asyncio.run(loop._build_once("esp32dev"))
    output = result["output"]
    assert output["error_code"] == "LOCAL_SOURCES_INCOMPLETE"
    assert output["source_directory"] == "src" and output["execution_attempted"] is False
    assert not output["terminal"] and not manager.calls
    loop.observations = [result, *({"tool": "read_file", "status": "completed", "output": {"path": f"old-{i}"}}
        for i in range(30))]
    context, _ = loop._compacted_observations()
    focus = next(item["output"] for item in context if item["tool"] == "source_repair_focus")
    assert "source files" in focus["instruction"] and "parent directories" in focus["instruction"]
    # This must not engage the platformio.ini-only configuration barrier.
    assert not any(item["tool"] == "build_repair_focus" for item in context)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("void setup() {}\nvoid loop() {}\n")
    context, _ = loop._compacted_observations()
    assert not any(item["tool"] == "source_repair_focus" for item in context)


def test_missing_custom_source_directory_keeps_the_configured_layout(tmp_path):
    from backend.services.platformio_service import PlatformIOService, PlatformIOProjectError
    from tests.unit.test_live_session_recovery import VALID_INI
    from tests.unit.test_platformio_service import FakeSubprocessManager
    (tmp_path / "platformio.ini").write_text("[platformio]\nsrc_dir=firmware/app\n" + VALID_INI)
    service = PlatformIOService(FakeSubprocessManager())
    with pytest.raises(PlatformIOProjectError) as caught:
        service.validate_project(tmp_path)
    assert getattr(caught.value, "code", None) == "LOCAL_SOURCES_INCOMPLETE"
    assert caught.value.details["source_directory"] == "firmware/app"
