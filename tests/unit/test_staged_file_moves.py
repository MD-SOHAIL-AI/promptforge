from pathlib import Path

import pytest

from backend.agent_runtime.tool_contracts import ToolCall, ToolName
from backend.agent_runtime.tool_executor import ForgeXToolExecutor
from backend.agent_runtime.tool_policy import product_agent_policy, ToolPermissionPolicy, ToolPolicyError
from backend.changes.service import ChangeSetService, ChangeSetError


def fixture(tmp_path):
    active = tmp_path / "project"
    active.mkdir()
    (active / "old.cpp").write_bytes(b"int existing;\n")
    changes = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
    baseline = changes.inspect_workspace(active)
    stage = changes.create_stage()
    executor = ForgeXToolExecutor(read_root=active, write_root=stage, policy=product_agent_policy())
    return active, stage, changes, baseline, executor


def changeset(active, stage, changes, baseline, executor, paths):
    return changes.create_from_stage(provider_id="test", workspace_root=active,
        stage_root=stage, baseline=baseline, authorized_paths=paths, deleted_paths=executor.deleted_paths)


def test_move_is_staged_reviewed_and_reversible_after_restart(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    executor.execute(ToolCall(ToolName.MOVE_FILE, {"path": "old.cpp", "destination": "src/new.cpp"}))
    assert (active / "old.cpp").read_bytes() == b"int existing;\n"
    assert not (active / "src/new.cpp").exists()
    assert executor.execute(ToolCall(ToolName.LIST_FILES, {})) == ("src/new.cpp",)
    assert executor.execute(ToolCall(ToolName.LIST_FILES, {"path": "src"})) == ("src/new.cpp",)
    assert executor.execute(ToolCall(ToolName.GLOB_FILES, {"path": "src", "pattern": "*.cpp"})) == ("src/new.cpp",)
    assert executor.execute(ToolCall(ToolName.GREP_SEARCH, {"path": "src", "query": "existing"}))[0]["path"] == "src/new.cpp"
    result = changeset(active, stage, changes, baseline, executor, {"old.cpp", "src/new.cpp"})
    assert {item.path: item.change_type for item in result.changed_files} == {"old.cpp": "deleted", "src/new.cpp": "created"}
    from backend.agent_runtime.risk import RiskAssessmentService
    assessment = RiskAssessmentService().assess(result, explicit_edit_authorized=True)
    assert assessment.user_approval_required and assessment.level == "high"
    assert not assessment.scoped_auto_apply_allowed
    reopened = ChangeSetService(state_root=changes.state_root, staging_root=changes.staging_root)
    reopened.apply(result.change_set_id)
    assert not (active / "old.cpp").exists()
    assert (active / "src/new.cpp").read_bytes() == b"int existing;\n"
    reopened.undo(result.change_set_id)
    assert (active / "old.cpp").read_bytes() == b"int existing;\n"
    assert not (active / "src/new.cpp").exists()


def test_deletion_undo_does_not_overwrite_recreated_file(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))
    result = changeset(active, stage, changes, baseline, executor, {"old.cpp"})
    changes.apply(result.change_set_id)
    (active / "old.cpp").write_bytes(b"user replacement")
    with pytest.raises(ChangeSetError, match="recreated"):
        changes.undo(result.change_set_id)
    assert (active / "old.cpp").read_bytes() == b"user replacement"


def test_deleted_path_must_remain_absent_from_reviewed_stage(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))
    result = changeset(active, stage, changes, baseline, executor, {"old.cpp"})
    (stage / "old.cpp").write_bytes(b"tampered")
    with pytest.raises(ChangeSetError) as error:
        changes.apply(result.change_set_id)
    assert error.value.code == "CHANGE_STAGE_TAMPERED"
    assert (active / "old.cpp").read_bytes() == b"int existing;\n"


def test_move_never_overwrites_destination_and_readonly_rejects_delete(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    with pytest.raises(ValueError, match="destination_exists"):
        executor.execute(ToolCall(ToolName.MOVE_FILE, {"path": "old.cpp", "destination": "old.cpp"}))
    assert executor.deleted_paths == set()
    readonly = ForgeXToolExecutor(read_root=active, write_root=stage, policy=ToolPermissionPolicy())
    with pytest.raises(ToolPolicyError):
        readonly.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))


@pytest.mark.parametrize("destination", ["../outside.cpp", "C:/outside.cpp", ".env", "secrets.json"])
def test_move_rejects_unsafe_destinations_before_mutation(tmp_path, destination):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    with pytest.raises(ToolPolicyError):
        executor.execute(ToolCall(ToolName.MOVE_FILE, {"path": "old.cpp", "destination": destination}))
    assert executor.deleted_paths == set()
    assert list(stage.iterdir()) == []


def test_delete_then_write_same_path_is_modification(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))
    executor.execute(ToolCall(ToolName.WRITE_FILE, {"path": "old.cpp", "content": "int replaced;\n"}))
    result = changeset(active, stage, changes, baseline, executor, {"old.cpp"})
    assert len(result.changed_files) == 1
    assert result.changed_files[0].change_type == "modified"
    changes.apply(result.change_set_id)
    changes.undo(result.change_set_id)
    assert (active / "old.cpp").read_bytes() == b"int existing;\n"


def test_deletion_without_explicit_authorization_is_rejected(tmp_path):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))
    with pytest.raises(ChangeSetError) as error:
        changeset(active, stage, changes, baseline, executor, set())
    assert error.value.code == "CHANGE_DELETE_INVALID"


def test_runtime_collects_both_move_paths_and_emits_deletion_count(tmp_path):
    from backend.agent_runtime import RuntimeClassification
    from backend.agent_runtime.tool_contracts import ProductToolPlan
    from tests.unit.test_tool_runtime import SequencePlanner, make_runtime, plan
    runtime, active, changes = make_runtime(tmp_path)
    move = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "move", "final": False,
        "tool_calls": [{"id": "move", "type": "move_file", "arguments": {"path": "README.txt", "destination": "docs/README.txt"}}]})
    result = runtime.run_product(task="Move the readme into docs", planner=SequencePlanner([move, plan(final=True)]), policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    assert result.created_file_count == result.deleted_file_count == 1
    assert (active / "README.txt").exists()
    changes.apply(result.change_set_id)
    assert (active / "docs/README.txt").exists()
    changes.undo(result.change_set_id)
    assert (active / "README.txt").exists()


def test_case_variant_delete_on_case_insensitive_hosts(tmp_path):
    import os
    if os.path.normcase("A") != os.path.normcase("a"):
        pytest.skip("case-sensitive host")
    active, stage, changes, baseline, executor = fixture(tmp_path)
    result = executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "OLD.cpp"}))
    assert result["path"] == "old.cpp"
    assert executor.execute(ToolCall(ToolName.LIST_FILES, {})) == ()
    change = changeset(active, stage, changes, baseline, executor, {"old.cpp"})
    changes.apply(change.change_set_id)
    assert not (active / "old.cpp").exists()


def test_partial_delete_apply_failure_restores_all_files(tmp_path, monkeypatch):
    active, stage, changes, baseline, executor = fixture(tmp_path)
    (active / "second.cpp").write_bytes(b"int second;\n")
    baseline = changes.inspect_workspace(active)
    for name in ("old.cpp", "second.cpp"):
        executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": name}))
    change = changeset(active, stage, changes, baseline, executor, {"old.cpp", "second.cpp"})
    unlink = Path.unlink
    def fail_second(path, *args, **kwargs):
        if path == active / "second.cpp":
            raise OSError("injected deletion failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(ChangeSetError) as error:
        changes.apply(change.change_set_id)
    assert error.value.code == "CHANGE_APPLY_FAILED"
    assert (active / "old.cpp").read_bytes() == b"int existing;\n"
    assert (active / "second.cpp").read_bytes() == b"int second;\n"


@pytest.mark.parametrize("operation", ["move", "delete", "rewrite"])
def test_mixed_case_edit_and_delete_preserve_baseline_identity(tmp_path, operation):
    import os
    if os.path.normcase("A") != os.path.normcase("a"):
        pytest.skip("case-sensitive host")
    active, stage, changes, baseline, executor = fixture(tmp_path)
    (active / "old.cpp").rename(active / "Old.cpp")
    baseline = changes.inspect_workspace(active)
    executor.execute(ToolCall(ToolName.WRITE_FILE, {"path": "old.cpp", "content": "int edited;\n"}))
    if operation == "move":
        executor.execute(ToolCall(ToolName.MOVE_FILE, {"path": "old.cpp", "destination": "new.cpp"}))
    else:
        executor.execute(ToolCall(ToolName.DELETE_FILE, {"path": "old.cpp"}))
        if operation == "rewrite":
            executor.execute(ToolCall(ToolName.WRITE_FILE, {"path": "old.cpp", "content": "int recreated;\n"}))
    change = changeset(active, stage, changes, baseline, executor, {"old.cpp", "new.cpp"})
    kinds = {item.path: item.change_type for item in change.changed_files}
    assert kinds["Old.cpp"] == ("modified" if operation == "rewrite" else "deleted")
    changes.apply(change.change_set_id)
    assert (active / "Old.cpp").exists() == (operation == "rewrite")
    changes.undo(change.change_set_id)
    assert (active / "Old.cpp").read_bytes() == b"int existing;\n"
