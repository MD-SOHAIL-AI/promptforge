"""Backup and rollback safety checks using isolated filesystem fixtures."""

import os
import subprocess
from contextlib import ExitStack, contextmanager

import pytest

import backend.changes.service as service_module
from backend.changes.service import ChangeSetError, ChangeSetService


def prepare(tmp_path, *, originals=None, staged=None, deleted=()):
    active = tmp_path / "active"
    active.mkdir()
    for name, content in (originals or {}).items():
        path = active / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    service = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
    baseline = service.inspect_workspace(active)
    stage = service.create_stage()
    for name, content in (staged or {}).items():
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    change = service.create_from_stage(
        provider_id="test", workspace_root=active, stage_root=stage,
        baseline=baseline, authorized_paths={p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()} | set(deleted),
        deleted_paths=deleted,
    )
    return active, stage, service, change


@contextmanager
def directory_alias(link, target):
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield
    finally:
        if os.name == "nt":
            os.rmdir(link)
        else:
            link.unlink()


@pytest.mark.parametrize("inside_stage", [False, True])
def test_apply_rejects_backup_root_alias_without_overwriting(tmp_path, inside_stage):
    active, stage, service, change = prepare(
        tmp_path, originals={"old.cpp": b"original"}, deleted=("old.cpp",),
    )
    destination = (stage if inside_stage else tmp_path) / "destination"
    destination.mkdir()
    sentinel = destination / "old.cpp"
    sentinel.write_bytes(b"unrelated")
    with directory_alias(stage / "__undo__", destination):
        with pytest.raises(ChangeSetError):
            service.apply(change.change_set_id)
        assert sentinel.read_bytes() == b"unrelated"
        assert (active / "old.cpp").read_bytes() == b"original"
        assert service.get(change.change_set_id).status == "failed"


@pytest.mark.parametrize("namespace", ["__undo__", "__UNDO__", "__undo__."])
def test_apply_rejects_reviewed_backup_namespace_alias(tmp_path, namespace):
    active, stage, service, change = prepare(
        tmp_path, staged={f"{namespace}/new.cpp": b"new"},
    )
    with pytest.raises(ChangeSetError) as error:
        service.apply(change.change_set_id)
    assert error.value.code == "CHANGE_UNDO_BACKUP_INVALID"
    assert list(active.iterdir()) == []


@pytest.mark.parametrize("nested", [False, True])
def test_undo_rejects_backup_alias_before_unlinking_created_file(tmp_path, nested):
    active, stage, service, change = prepare(
        tmp_path, originals={"src/old.cpp": b"original"},
        staged={"new.cpp": b"new"}, deleted=("src/old.cpp",),
    )
    service.apply(change.change_set_id)
    backup = stage / "__undo__"
    link = backup / "src" if nested else backup
    saved = stage / "saved-backups"
    link.rename(saved)
    with directory_alias(link, saved):
        with pytest.raises(ChangeSetError) as error:
            service.undo(change.change_set_id)
        assert error.value.code == "CHANGE_UNDO_BACKUP_INVALID"
        assert (active / "new.cpp").read_bytes() == b"new"
        assert not (active / "src/old.cpp").exists()
        assert service.get(change.change_set_id).status == "applied"


def test_changeset_error_rolls_back_prior_deletion_and_persists_failure(tmp_path, monkeypatch):
    active, stage, service, change = prepare(
        tmp_path, originals={"a.cpp": b"a", "b.cpp": b"b"},
        deleted=("a.cpp", "b.cpp"),
    )
    original_check = service_module._reject_symlink_components
    injected = False

    def check(root, path):
        nonlocal injected
        if root == active and not (active / "a.cpp").exists() and not injected:
            injected = True
            raise ChangeSetError("CHANGE_PATH_SYMLINK", "Injected safety failure")
        return original_check(root, path)

    monkeypatch.setattr(service_module, "_reject_symlink_components", check)
    with pytest.raises(ChangeSetError) as error:
        service.apply(change.change_set_id)
    assert error.value.code == "CHANGE_APPLY_FAILED"
    assert injected
    assert (active / "a.cpp").read_bytes() == b"a"
    assert (active / "b.cpp").read_bytes() == b"b"
    reopened = ChangeSetService(state_root=service.state_root, staging_root=service.staging_root)
    assert reopened.get(change.change_set_id).status == "failed"


def test_undo_does_not_unlink_through_active_directory_alias(tmp_path):
    active, stage, service, change = prepare(tmp_path, staged={"src/new.cpp": b"new"})
    service.apply(change.change_set_id)
    saved = active / "saved"
    (active / "src").rename(saved)
    with directory_alias(active / "src", saved):
        with pytest.raises(ChangeSetError):
            service.undo(change.change_set_id)
        assert (saved / "new.cpp").read_bytes() == b"new"
        assert service.get(change.change_set_id).status == "applied"


def test_apply_undo_still_restores_nested_deletion(tmp_path):
    active, stage, service, change = prepare(
        tmp_path, originals={"src/old.cpp": b"original"},
        staged={"new.cpp": b"new"}, deleted=("src/old.cpp",),
    )
    service.apply(change.change_set_id)
    service.undo(change.change_set_id)
    assert (active / "src/old.cpp").read_bytes() == b"original"
    assert not (active / "new.cpp").exists()


def test_apply_rollback_does_not_unlink_through_replaced_parent(tmp_path, monkeypatch):
    active, stage, service, change = prepare(
        tmp_path, staged={"src/a.cpp": b"new", "z.cpp": b"last"},
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.cpp").write_bytes(b"unrelated")
    original_write = service_module._atomic_write_bytes
    with ExitStack() as aliases:
        def write(path, data):
            if path == active / "z.cpp":
                (active / "src").rename(active / "saved")
                aliases.enter_context(directory_alias(active / "src", outside))
                raise ChangeSetError("CHANGE_PATH_SYMLINK", "Injected replacement")
            return original_write(path, data)

        monkeypatch.setattr(service_module, "_atomic_write_bytes", write)
        with pytest.raises(ChangeSetError) as error:
            service.apply(change.change_set_id)
        assert error.value.code == "CHANGE_APPLY_FAILED"
        assert (outside / "a.cpp").read_bytes() == b"unrelated"
        assert service.get(change.change_set_id).status == "failed"
