from __future__ import annotations

from pathlib import Path

import pytest

from backend.changes import ChangeSetError, ChangeSetService
from backend.changes.service import is_safe_relative_path


def make_service(tmp_path: Path) -> ChangeSetService:
    return ChangeSetService(
        state_root=tmp_path / "state",
        staging_root=tmp_path / "staging",
    )


def prepare_change(
    tmp_path: Path,
    *,
    relative: str = "src/main.cpp",
    before: str | None = "old\n",
    after: str = "new\n",
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / relative
    if before is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(before, encoding="utf-8")

    service = make_service(tmp_path)
    baseline = service.inspect_workspace(workspace)
    stage = service.create_stage("run-1")
    staged = stage / relative
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(after, encoding="utf-8")
    change_set = service.create_from_stage(
        provider_id="test-provider",
        workspace_root=workspace,
        stage_root=stage,
        baseline=baseline,
        authorized_paths={relative},
    )
    return service, workspace, target, stage, change_set


def test_changeset_stages_without_mutating_workspace_then_applies(tmp_path: Path) -> None:
    service, _, target, _, change_set = prepare_change(tmp_path)
    assert target.read_text(encoding="utf-8") == "old\n"
    assert change_set.status == "pending"
    assert change_set.changed_files[0].change_type == "modified"
    assert "-old" in (change_set.changed_files[0].diff_preview or "")
    assert "+new" in (change_set.changed_files[0].diff_preview or "")

    applied = service.apply(change_set.change_set_id)
    assert applied.status == "applied"
    assert target.read_text(encoding="utf-8") == "new\n"


def test_changeset_can_apply_new_file_and_undo_it(tmp_path: Path) -> None:
    service, _, target, _, change_set = prepare_change(tmp_path, relative="new.txt", before=None, after="created\n")
    assert not target.exists()
    service.apply(change_set.change_set_id)
    assert target.read_text(encoding="utf-8") == "created\n"

    undone = service.undo(change_set.change_set_id)
    assert undone.status == "undone"
    assert not target.exists()


def test_changeset_undo_restores_modified_file(tmp_path: Path) -> None:
    service, _, target, _, change_set = prepare_change(tmp_path)
    service.apply(change_set.change_set_id)
    assert target.read_text(encoding="utf-8") == "new\n"

    undone = service.undo(change_set.change_set_id)
    assert undone.status == "undone"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_apply_refuses_workspace_conflict_before_writing(tmp_path: Path) -> None:
    service, _, target, _, change_set = prepare_change(tmp_path)
    target.write_text("user edit\n", encoding="utf-8")

    with pytest.raises(ChangeSetError) as exc:
        service.apply(change_set.change_set_id)
    assert exc.value.code == "CHANGE_CONFLICT"
    assert target.read_text(encoding="utf-8") == "user edit\n"
    assert service.get(change_set.change_set_id).status == "conflicted"


def test_apply_refuses_unrelated_workspace_revision_change(tmp_path: Path) -> None:
    service, workspace, target, _, change_set = prepare_change(tmp_path)
    (workspace / "unrelated.txt").write_text("external edit\n", encoding="utf-8")

    with pytest.raises(ChangeSetError) as exc:
        service.apply(change_set.change_set_id)
    assert exc.value.code == "CHANGE_CONFLICT"
    assert exc.value.details["conflict_type"] == "workspace_revision"
    assert target.read_text(encoding="utf-8") == "old\n"


def test_apply_refuses_tampered_stage(tmp_path: Path) -> None:
    service, _, target, stage, change_set = prepare_change(tmp_path)
    (stage / "src/main.cpp").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ChangeSetError) as exc:
        service.apply(change_set.change_set_id)
    assert exc.value.code == "CHANGE_STAGE_TAMPERED"
    assert target.read_text(encoding="utf-8") == "old\n"
    assert service.get(change_set.change_set_id).status == "failed"


def test_undo_refuses_post_apply_user_edit(tmp_path: Path) -> None:
    service, _, target, _, change_set = prepare_change(tmp_path)
    service.apply(change_set.change_set_id)
    target.write_text("user after apply\n", encoding="utf-8")

    with pytest.raises(ChangeSetError) as exc:
        service.undo(change_set.change_set_id)
    assert exc.value.code == "CHANGE_UNDO_CONFLICT"
    assert target.read_text(encoding="utf-8") == "user after apply\n"


def test_unexpected_staged_file_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = make_service(tmp_path)
    baseline = service.inspect_workspace(workspace)
    stage = service.create_stage("run-extra")
    (stage / "allowed.txt").write_text("ok\n", encoding="utf-8")
    (stage / "rogue.txt").write_text("rogue\n", encoding="utf-8")

    with pytest.raises(ChangeSetError) as exc:
        service.create_from_stage(
            provider_id="test",
            workspace_root=workspace,
            stage_root=stage,
            baseline=baseline,
            authorized_paths={"allowed.txt"},
        )
    assert exc.value.code == "CHANGE_EXTRA_FILES"


def test_generated_internal_manifest_is_not_part_of_changeset(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = make_service(tmp_path)
    baseline = service.inspect_workspace(workspace)
    stage = service.create_stage("run-manifest")
    (stage / ".promptforge-project.json").write_text("{}\n", encoding="utf-8")
    (stage / "platformio.ini").write_text("[env:test]\n", encoding="utf-8")

    change_set = service.create_from_stage(
        provider_id="test",
        workspace_root=workspace,
        stage_root=stage,
        baseline=baseline,
        authorized_paths={".promptforge-project.json", "platformio.ini"},
    )

    assert [item.path for item in change_set.changed_files] == ["platformio.ini"]


@pytest.mark.parametrize(
    "path",
    [
        "../escape.txt",
        "/absolute.txt",
        "C:/absolute.txt",
        "C:\\absolute.txt",
        ".git/config",
        ".env",
        "safe/../../escape.txt",
        "safe\\file.txt",
    ],
)
def test_unsafe_relative_paths_are_rejected(path: str) -> None:
    assert is_safe_relative_path(path) is False


def test_discard_removes_stage_and_persists_terminal_status(tmp_path: Path) -> None:
    service, _, _, stage, change_set = prepare_change(tmp_path)
    discarded = service.discard(change_set.change_set_id)
    assert discarded.status == "discarded"
    assert not stage.exists()
    reloaded = make_service(tmp_path).get(change_set.change_set_id)
    assert reloaded.status == "discarded"
