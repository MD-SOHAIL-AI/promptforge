from __future__ import annotations

import threading
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


def test_tool_results_return_only_safe_metadata_to_planner(tmp_path: Path) -> None:
    runtime, _, _ = make_runtime(tmp_path)
    planner = SequencePlanner([plan(), plan(final=True)])
    result = runtime.run_product(task="write", planner=planner, policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    second_turn_results = planner.seen_results[1]
    assert second_turn_results[-1]["status"] == "completed"
    assert "content" not in second_turn_results[-1]


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


def test_max_turns_discards_stage_and_creates_no_changeset(tmp_path: Path) -> None:
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
    assert result.change_set_id is None
    assert service.list() == ()


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
