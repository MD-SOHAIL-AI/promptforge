from backend.agent_runtime import ForgeXToolRuntime, ProductRuntimeLimits, RuntimeClassification, product_agent_policy
from backend.agent_runtime.tool_contracts import ProductToolPlan
from backend.agent_runtime.progress_state import ProgressState
from backend.changes import ChangeSetService


class Planner:
    provider_id = "test"
    supports_durable_continuation = True

    def __init__(self, paths):
        self.paths = iter(paths)

    def request_turn(self, **kwargs):
        path = next(self.paths, None)
        return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Inspection complete" if path is None else "Reading project",
            "tool_calls": [] if path is None else [{"id": "read-"+str(kwargs["turn"]), "type": "read_file", "arguments": {"path": path}}], "final": path is None})


def runtime(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    for i in range(16):
        (active / f"{i}.txt").write_text(f"source information {i}")
    service = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stage")
    return ForgeXToolRuntime(active_workspace_root=active, change_service=service)


def test_unique_reads_continue_past_turn_slice_and_checkpoint_progress(tmp_path):
    events = []
    result = runtime(tmp_path).run_product(task="Inspect project", planner=Planner([f"{i}.txt" for i in range(16)]),
        policy=product_agent_policy(), limits=ProductRuntimeLimits(max_turns=2), event_callback=events.append, allow_no_changes_success=True)
    assert result.classification is RuntimeClassification.PASS
    assert result.tool_execution_count == 16
    checkpoints = [e["_resume_checkpoint"] for e in events if e.get("_resume_checkpoint")]
    assert checkpoints[-1]["progress_state"]["last_progress_turn"] == 16
    assert checkpoints[-1]["continuation_summary"]["objective"] == "Inspect project"


def test_identical_reads_stop_and_progress_fingerprints_survive_restore(tmp_path):
    result = runtime(tmp_path).run_product(task="Inspect project", planner=Planner(["0.txt"] * 12), policy=product_agent_policy(),
        limits=ProductRuntimeLimits(max_turns=2), allow_no_changes_success=True)
    assert result.classification is RuntimeClassification.NO_PROGRESS
    assert result.tool_execution_count == 4
    state = ProgressState()
    observation = {"tool": "read_file", "status": "completed", "output": "same information", "call_id": "old"}
    assert state.observe(observation)
    restored = ProgressState(state.snapshot())
    assert not restored.observe({**observation, "call_id": "new"})


def test_progress_continuation_still_respects_tool_budget(tmp_path):
    result = runtime(tmp_path).run_product(task="Inspect project", planner=Planner([f"{i}.txt" for i in range(16)]),
        policy=product_agent_policy(), limits=ProductRuntimeLimits(max_turns=2, max_total_tool_calls=3), allow_no_changes_success=True)
    assert result.classification is RuntimeClassification.LIMIT_EXCEEDED
    assert result.tool_execution_count == 3
