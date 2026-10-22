from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from backend.agent_runtime import ForgeXToolRuntime, ProductRuntimeLimits, RuntimeClassification, product_agent_policy
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan, ToolCall
from backend.agent_runtime.tool_policy import ToolPolicyError
from backend.changes import ChangeSetService


def plan(*, path: str = "safe.txt", content: str = "safe\n", final: bool = False, call_id: str = "call_1") -> ProductToolPlan:
    if final:
        return ProductToolPlan.parse({
            "version": PRODUCT_TOOLPLAN_VERSION,
            "summary": "done",
            "tool_calls": [],
            "final": True,
        })
    return ProductToolPlan.parse({
        "version": PRODUCT_TOOLPLAN_VERSION,
        "summary": "write",
        "tool_calls": [{"id": call_id, "type": "write_file", "path": path, "content": content}],
        "final": False,
    })


class SequencePlanner:
    provider_id = "sequence"
    model_id = "test"

    def __init__(self, turns, callback=None):
        self.turns = list(turns)
        self.callback = callback
        self.seen_results = []

    def request_turn(self, **kwargs):
        self.seen_results.append(tuple(kwargs["tool_results"]))
        if self.callback:
            self.callback(kwargs)
        if not self.turns:
            return plan(final=True)
        return self.turns.pop(0)


def make_runtime(tmp_path: Path):
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.txt").write_text("baseline\n", encoding="utf-8")
    service = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "staging")
    runtime = ForgeXToolRuntime(active_workspace_root=active, change_service=service)
    return runtime, active, service


def test_valid_write_creates_changeset_without_touching_active_workspace(tmp_path: Path) -> None:
    runtime, active, changes = make_runtime(tmp_path)
    planner = SequencePlanner([plan(path="src/main.cpp", content="int main(){}\n"), plan(final=True)])

    result = runtime.run_product(task="create file", planner=planner, policy=product_agent_policy())

    assert result.classification is RuntimeClassification.PASS
    assert result.change_set_id
    assert result.active_workspace_unchanged is True
    assert result.created_file_count == 1
    assert not (active / "src/main.cpp").exists()
    change_set = changes.get(result.change_set_id)
    assert change_set.status == "pending"
    assert change_set.changed_files[0].path == "src/main.cpp"


def test_staged_runtime_binds_cumulative_job_deadline_to_model_turns(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    class DeadlinePlanner(SequencePlanner):
        def bind_deadline(self, deadline):
            self.deadline = deadline
    planner = DeadlinePlanner([plan(), plan(final=True)])
    result = runtime.run_product(task="create file", planner=planner,
        policy=product_agent_policy(), limits=ProductRuntimeLimits(max_runtime_seconds=900),
        budget_consumed={"elapsed_seconds": 600})
    assert result.classification is RuntimeClassification.PASS
    assert 290 < planner.deadline - time.monotonic() < 296


def test_grant_remains_valid_after_five_minutes_within_job_deadline(tmp_path, monkeypatch):
    from backend.agent_runtime.capabilities import ToolPolicyEngine
    import backend.agent_runtime.capabilities as capabilities
    clock = [1000.0]
    monkeypatch.setattr(capabilities.time, "monotonic", lambda: clock[0])
    runtime, active, _ = make_runtime(tmp_path)
    def delayed_model(_):
        clock[0] += 301
    planner = SequencePlanner([plan(), plan(final=True)], callback=delayed_model)
    result = runtime.run_product(task="Create a file", planner=planner,
        policy=product_agent_policy(), policy_engine=ToolPolicyEngine(),
        limits=ProductRuntimeLimits(max_runtime_seconds=900))
    assert result.classification is RuntimeClassification.PASS
    assert result.created_file_count == 1
    assert not (active / "safe.txt").exists()


def test_turn_exhaustion_keeps_observed_provider_diagnostics(tmp_path):
    runtime, _, _ = make_runtime(tmp_path)
    class Planner(SequencePlanner):
        def to_safe_metadata(self):
            return {"physical_request_count": 1, "http_status": 200,
                    "request_reached_provider": True}
    listing = ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION,
        "summary": "Inspect files", "tool_calls": [{"id": "list-1", "type": "list_files",
        "arguments": {"path": "."}}], "final": False})
    result = runtime.run_product(task="Inspect files", planner=Planner([listing]),
        policy=product_agent_policy(), limits=ProductRuntimeLimits(max_turns=1))
    assert result.classification is RuntimeClassification.MAX_TURNS
    # Stop without fabricating that the successful provider call never happened.
    assert result.provider_diagnostics["http_status"] == 200
    assert result.provider_diagnostics["request_reached_provider"] is True


def test_missing_project_file_can_be_created_in_the_same_run(tmp_path: Path) -> None:
    runtime, active, changes = make_runtime(tmp_path)
    read_missing = ProductToolPlan.parse({
        "version": PRODUCT_TOOLPLAN_VERSION,
        "summary": "Inspect the header",
        "tool_calls": [{"id": "read_header", "type": "read_file", "arguments": {"path": "include/Pins.h"}}],
        "final": False,
    })
    planner = SequencePlanner([
        read_missing,
        plan(path="include/Pins.h", content="#pragma once\n", call_id="write_header"),
        plan(final=True),
    ])
    result = runtime.run_product(task="Create the missing header", planner=planner, policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    assert planner.seen_results[1][-1]["classification"] == RuntimeClassification.FILE_NOT_FOUND.value
    assert result.change_set_id is not None
    assert changes.get(result.change_set_id).changed_files[0].path == "include/Pins.h"
    assert not (active / "include/Pins.h").exists()


def test_tool_results_return_only_safe_metadata_to_planner(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    planner = SequencePlanner([plan(), plan(final=True)])
    result = runtime.run_product(task="write", planner=planner, policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    second_turn_results = planner.seen_results[1]
    assert second_turn_results[-1]["status"] == "completed"
    assert "content" not in second_turn_results[-1]


def test_tool_lifecycle_events_include_correlation_and_summaries(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    events: list[dict[str, object]] = []
    result = runtime.run_product(
        task="write",
        planner=SequencePlanner([plan(path="src/main.cpp", content="int main(){}\n"), plan(final=True)]),
        policy=product_agent_policy(),
        capability_run_id="run-123",
        event_callback=events.append,
    )

    assert result.classification is RuntimeClassification.PASS
    turn_started = next(event for event in events if event["event_type"] == "agent.turn.started")
    tool_started = next(event for event in events if event["event_type"] == "tool.started")
    tool_completed = next(event for event in events if event["event_type"] == "tool.completed")
    assert turn_started["turn_id"] == "run-123:turn-1"
    assert tool_started["tool_call_id"] == "call_1"
    assert tool_started["turn_id"] == "run-123:turn-1"
    assert tool_started["arguments"] == {"path": "src/main.cpp", "content_bytes": len("int main(){}\n")}
    assert tool_completed["duration_ms"] >= 0
    assert tool_completed["result"]["path"] == "src/main.cpp"


@pytest.mark.parametrize("path", ["/absolute.txt", "C:/absolute.txt", "../parent.txt", "safe/../../parent.txt", "safe\\file.txt"])
def test_unsafe_paths_preserve_path_classification(tmp_path: Path, path: str) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    result = runtime.run_product(
        task="unsafe",
        planner=SequencePlanner([plan(path=path), plan(final=True)]),
        policy=product_agent_policy(),
    )
    assert result.classification is RuntimeClassification.PATH_UNSAFE
    assert result.change_set_id is None


def test_sensitive_path_is_policy_denied(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    result = runtime.run_product(
        task="unsafe",
        planner=SequencePlanner([plan(path=".env", content="SECRET=x\n"), plan(final=True)]),
        policy=product_agent_policy(),
    )
    assert result.classification in {RuntimeClassification.PATH_UNSAFE, RuntimeClassification.POLICY_DENIED}
    assert result.change_set_id is None


def test_oversized_content_preserves_content_classification(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    result = runtime.run_product(
        task="large",
        planner=SequencePlanner([plan(content="x" * 200_000), plan(final=True)]),
        policy=product_agent_policy(max_bytes_per_file=1024),
    )
    assert result.classification is RuntimeClassification.CONTENT_INVALID


def test_same_content_returns_no_changes(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    result = runtime.run_product(
        task="no-op",
        planner=SequencePlanner([plan(path="README.txt", content="baseline\n"), plan(final=True)]),
        policy=product_agent_policy(),
    )
    assert result.classification is RuntimeClassification.NO_CHANGES
    assert result.change_set_id is None


def test_unexpected_stage_file_is_rejected(tmp_path: Path) -> None:
    runtime, _, service = make_runtime(tmp_path)

    def inject(kwargs):
        if kwargs["turn"] != 2:
            return
        stages = [p for p in service.staging_root.iterdir() if p.is_dir()]
        assert len(stages) == 1
        (stages[0] / "rogue.txt").write_text("rogue\n", encoding="utf-8")

    planner = SequencePlanner([plan(path="safe.txt"), plan(final=True)], callback=inject)
    result = runtime.run_product(task="extra", planner=planner, policy=product_agent_policy())
    assert result.classification is RuntimeClassification.EXTRA_CHANGES
    assert result.change_set_id is None


def test_active_workspace_mutation_aborts_run(tmp_path: Path) -> None:
    runtime, active, _ = make_runtime(tmp_path)

    def mutate(kwargs):
        if kwargs["turn"] == 2:
            (active / "README.txt").write_text("external mutation\n", encoding="utf-8")

    result = runtime.run_product(
        task="mutation",
        planner=SequencePlanner([plan(), plan(final=True)], callback=mutate),
        policy=product_agent_policy(),
    )
    assert result.classification is RuntimeClassification.UNSAFE_ABORTED
    assert result.change_set_id is None


def test_max_turns_retains_isolated_recovery_draft(tmp_path: Path) -> None:
    runtime, _, service = make_runtime(tmp_path)

    class EndlessPlanner:
        provider_id = "endless"
        model_id = "test"

        def request_turn(self, **kwargs):
            turn = kwargs["turn"]
            return plan(path=f"file{turn}.txt", call_id=f"call_{turn}")

    result = runtime.run_product(
        task="bounded",
        planner=EndlessPlanner(),
        policy=product_agent_policy(),
        limits=ProductRuntimeLimits(max_turns=2),
    )
    assert result.classification is RuntimeClassification.MAX_TURNS
    assert result.change_set_id
    draft = service.get(result.change_set_id)
    assert draft.status == "draft"
    assert {item.path for item in draft.changed_files} == {f"file{index}.txt" for index in range(1, 15)}
    assert result.created_file_count == 14
    assert result.modified_file_count == result.deleted_file_count == 0
    assert not (runtime.active_workspace_root / "file1.txt").exists()


def test_pre_cancelled_run_executes_nothing(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    cancelled = threading.Event()
    cancelled.set()
    result = runtime.run_product(
        task="cancel",
        planner=SequencePlanner([plan(), plan(final=True)]),
        policy=product_agent_policy(),
        cancel_event=cancelled,
    )
    assert result.classification is RuntimeClassification.CANCELLED
    assert result.tool_execution_count == 0


def test_unknown_tool_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ToolCall.parse({"tool": "shell", "arguments": {}})


def test_policy_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation unavailable on this host")
    with pytest.raises(ToolPolicyError) as exc:
        product_agent_policy().resolve_path(root, "link/file.txt", must_exist=False)
    assert exc.value.classification is RuntimeClassification.PATH_UNSAFE


def test_high_confidence_secret_content_is_not_written_by_agent(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    result = runtime.run_product(
        task="secret",
        planner=SequencePlanner([plan(path="src/config.cpp", content='const char* key = "sk-abcdefghijklmnopqrstuvwxyz123456";\n'), plan(final=True)]),
        policy=product_agent_policy(),
    )
    assert result.classification is RuntimeClassification.POLICY_DENIED
    assert result.change_set_id is None


def test_high_confidence_secret_in_safe_named_file_is_not_exposed_to_agent(tmp_path: Path) -> None:
    runtime, active, _ = make_runtime(tmp_path)
    (active / "config.txt").write_text("token=ghp_abcdefghijklmnopqrstuvwxyzABCDE12345\n", encoding="utf-8")
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor
    from backend.agent_runtime.tool_contracts import ToolName

    stage = tmp_path / "overlay"
    stage.mkdir()
    executor = ForgeXToolExecutor(write_root=stage, read_root=active, policy=product_agent_policy())
    call = ToolCall(tool=ToolName.READ_FILE, arguments={"path": "config.txt"})
    with pytest.raises(ToolPolicyError) as exc:
        executor.execute(call)
    assert exc.value.reason == "secret_content_forbidden"


def test_policy_rejects_windows_reparse_point_attribute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agent_runtime.tool_policy as policy_module

    target = tmp_path / "reparse-target"
    target.write_text("data", encoding="utf-8")
    original_lstat = Path.lstat

    class StatProxy:
        def __init__(self, source):
            self._source = source
            self.st_mode = source.st_mode
            self.st_file_attributes = getattr(source, "st_file_attributes", 0) | 0x400

        def __getattr__(self, name):
            return getattr(self._source, name)

    def fake_lstat(path: Path):
        result = original_lstat(path)
        if path == target:
            return StatProxy(result)
        return result

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    with pytest.raises(ToolPolicyError, match="link_or_reparse_forbidden"):
        policy_module._reject_link_or_reparse(target)


def test_present_tool_can_declare_staged_deliverable_without_exposing_content(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_contracts import ToolName
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor

    runtime, active, _ = make_runtime(tmp_path)
    stage = tmp_path / "present-stage"
    stage.mkdir()
    (stage / "report.md").write_text("# Result\nSafe output\n", encoding="utf-8")
    executor = ForgeXToolExecutor(
        write_root=stage,
        read_root=active,
        policy=product_agent_policy(),
    )
    call = ToolCall(
        ToolName.PRESENT_FILES,
        {"files": [{"path": "report.md", "description": "Generated report"}]},
    )
    result = executor.execute(call)
    assert result["count"] == 1
    item = result["files"][0]
    assert item["path"] == "report.md"
    assert item["staged"] is True
    assert len(item["sha256"]) == 64
    assert "content" not in item


def test_canonical_settlement_precedes_tool_admission_and_commit(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    events: list[dict[str, object]] = []
    result = runtime.run_product(
        task="write",
        planner=SequencePlanner([plan(path="src/main.cpp", content="int main(){}\n"), plan(final=True)]),
        policy=product_agent_policy(),
        event_callback=events.append,
    )
    assert result.classification is RuntimeClassification.PASS
    names = [str(item.get("event_type")) for item in events]
    settled = names.index("model.attempt.settled")
    admitted = names.index("tool.invocation.admitted")
    started = names.index("tool.execution.started")
    committed = names.index("tool.result.committed")
    step_settled = names.index("step.settled")
    assert settled < admitted < started < committed < step_settled
    settlement = events[settled]
    assert settlement.get("attempt_id")
    assert settlement.get("turn") == 1
    assert settlement.get("step") == 1
