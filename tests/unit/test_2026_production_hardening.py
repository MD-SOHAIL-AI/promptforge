"""Contract checks for cross-process editing, crash recovery and safe renames."""
from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.api.routes.files import _update_external_file_locked
from backend.api.schemas.files import FileUpdateRequest
from backend.api.errors import APIError
from backend.contracts.generated_project import GeneratedProject, GeneratedFile
from backend.security.workspace_lock import workspace_lock, WorkspaceLockTimeout
from backend.services.project_import_service import ExternalProjectRecord
from backend.services.project_service import ProjectService, ProjectConflictError
from tests.workspace_lock_worker import hold_workspace_lock


def sample_project() -> GeneratedProject:
    return GeneratedProject(
        project_id='workspace-id', project_name='firmware', target_board='ESP32',
        framework='PlatformIO',
        files=(
            GeneratedFile('platformio.ini', '[env:esp32dev]\nboard = esp32dev\n', 'ini'),
            GeneratedFile('src/main.cpp', 'void setup() {}\nvoid loop() {}\n', 'cpp'),
        ), created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), metadata={},
    )


def test_workspace_lock_excludes_separate_process(tmp_path):
    ctx = multiprocessing.get_context('spawn')
    acquired, release = ctx.Event(), ctx.Event()
    process = ctx.Process(target=hold_workspace_lock, args=(str(tmp_path), acquired, release))
    process.start()
    try:
        assert acquired.wait(15), 'child did not acquire its workspace lock'
        with pytest.raises(WorkspaceLockTimeout):
            with workspace_lock(tmp_path, timeout=0.1):
                pass
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(2)
    assert process.exitcode == 0
    with workspace_lock(tmp_path, timeout=1):
        pass


def test_optimistic_structural_save_preserves_concurrent_edits(tmp_path):
    async def scenario():
        root = tmp_path / 'projects'
        first, second = ProjectService(root), ProjectService(root)
        original = sample_project()
        await first.create_project(original)
        snapshot = await first.load_project('firmware')
        previous = next(f for f in snapshot.files if f.path == 'src/main.cpp')
        new_content = 'void setup() { int new_value = 7; }\nvoid loop() {}\n'
        await second.save_file_if_revision_matches('firmware', 'workspace-id',
            'src/main.cpp', new_content, hashlib.sha256(previous.content.encode()).hexdigest())
        # A previously loaded snapshot must never undo the other editor's work.
        with pytest.raises(ProjectConflictError, match='PROJECT_REVISION_CONFLICT'):
            await first.save_project_if_current(snapshot, snapshot)
        result = await first.load_project('firmware')
        assert next(f for f in result.files if f.path == 'src/main.cpp').content == new_content
    asyncio.run(scenario())


def test_crash_during_directory_swap_is_recovered(tmp_path):
    async def scenario():
        root = tmp_path / 'projects'
        service = ProjectService(root)
        await service.create_project(sample_project())
        target, backup = root / 'firmware', root / '.firmware.backup'
        target.replace(backup)  # death after backup, before new stage publish
        assert not target.exists()
        restored = await ProjectService(root).load_project('firmware')
        assert restored.project_id == 'workspace-id'
        assert target.exists() and not backup.exists()
        # Death after stage publish: a complete target takes precedence.
        shutil.copytree(target, backup)
        restored = await ProjectService(root).load_project('firmware')
        assert restored.project_id == 'workspace-id'
        assert target.exists() and not backup.exists()
    asyncio.run(scenario())


def _external_project(root):
    now = datetime.now(timezone.utc)
    return ExternalProjectRecord('external-id','external','esp32','PlatformIO',str(root),now,now,1,{})


def test_external_rename_write_conflict_does_not_rename(tmp_path):
    (tmp_path / 'main.cpp').write_text('newer content', encoding='utf-8')
    old_revision = hashlib.sha256(b'older content').hexdigest()
    with pytest.raises(APIError) as exc:
        _update_external_file_locked(FileUpdateRequest(
            project_id='external-id', path='main.cpp', new_path='renamed.cpp',
            content='replacement', expected_revision=old_revision),
            _external_project(tmp_path), 'main.cpp', 'renamed.cpp')
    assert exc.value.status_code == 409
    assert (tmp_path / 'main.cpp').read_text() == 'newer content'
    assert not (tmp_path / 'renamed.cpp').exists()


def test_external_save_refuses_stale_revision(tmp_path):
    (tmp_path / 'main.cpp').write_text('newer content', encoding='utf-8')
    old_revision = hashlib.sha256(b'older content').hexdigest()
    with pytest.raises(APIError) as exc:
        _update_external_file_locked(FileUpdateRequest(
            project_id='external-id', path='main.cpp',
            content='replacement', expected_revision=old_revision),
            _external_project(tmp_path), 'main.cpp', None)
    assert exc.value.status_code == 409
    assert (tmp_path / 'main.cpp').read_text() == 'newer content'


def test_memory_detects_related_external_edit_without_dropping_unrelated_context(tmp_path):
    from backend.agent_runtime.memory import ProjectMemoryService
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    root = tmp_path / 'code'
    root.mkdir()
    (root / 'main.cpp').write_text('source version 1')
    (root / 'config.ini').write_text('config version 1')
    memory = ProjectMemoryService(AgentOrchestrationStore(tmp_path / 'memory.db'))
    memory.add_verified(project_id='project', kind='applied_change', value='updated main.cpp',
        source_type='changeset', source_id='changeset-1', source_payload='checksum',
        affected_paths=['main.cpp'], workspace_root=root, workspace_revision='workspace:one')
    (root / 'config.ini').write_text('config version 2')
    assert len(memory.active('project', current_workspace_revision='workspace:two', workspace_root=root)) == 1
    (root / 'main.cpp').write_text('source version 2')
    assert memory.active('project', current_workspace_revision='workspace:three', workspace_root=root) == ()


def test_corrupted_backup_never_replaces_live_project(tmp_path):
    async def scenario():
        root = tmp_path / 'projects'
        service = ProjectService(root)
        await service.create_project(sample_project())
        target, backup = root / 'firmware', root / '.firmware.backup'
        target.replace(backup)
        (backup / '.promptforge-project.json').write_text('{incomplete')
        with pytest.raises(Exception):
            await ProjectService(root).load_project('firmware')
        assert not target.exists() and backup.exists()
    asyncio.run(scenario())


def test_external_file_create_and_delete_use_safe_routes(tmp_path):
    from backend.api.routes.files import _create_external_entry_locked, _delete_external_entry_locked
    from backend.api.schemas.files import FileCreateRequest
    metadata = _external_project(tmp_path)
    created = _create_external_entry_locked(FileCreateRequest(project_id='external-id',
        path='src/new.cpp', content='int x = 1;'), metadata, 'src/new.cpp')
    assert any(item.path == 'src/new.cpp' for item in created.entries)
    assert (tmp_path / 'src' / 'new.cpp').read_text() == 'int x = 1;'
    removed = _delete_external_entry_locked(metadata, 'src/new.cpp')
    assert removed.deleted is True
    assert not (tmp_path / 'src' / 'new.cpp').exists()
