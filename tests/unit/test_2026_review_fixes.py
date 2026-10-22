"""Regression tests for security and correctness fixes from the October 20 review.

These tests intentionally avoid network providers and physical boards.
"""
import asyncio
import hashlib
from datetime import datetime, timezone

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.memory import ProjectMemoryService
from backend.agent_runtime.build_diagnostics import contained_diagnostic_path, parse_build_diagnostics
from backend.services.build_operations import BuildOperations
from backend.services.project_service import ProjectService, ProjectConflictError
from backend.contracts.generated_project import GeneratedProject, GeneratedFile
from backend.model_router import ProviderRegistry, ProviderSettingsStorage, MemoryCredentialStore


def test_decisions_are_superseded_across_sessions(tmp_path):
    service = ProjectMemoryService(AgentOrchestrationStore(tmp_path / 'agent.db'))
    for session_id, choice in [('one', 'cloud: MQTT'), ('two', 'cloud: ThingSpeak')]:
        service.remember_choices(project_id='p', session_id=session_id,
            contract={'revision': session_id, 'accepted_choices': [choice]})
    active = service.active('p')
    assert len(active) == 1
    assert active[0]['value'] == 'cloud: ThingSpeak'


def test_unrelated_file_does_not_invalidate_scoped_verified_memory(tmp_path):
    service = ProjectMemoryService(AgentOrchestrationStore(tmp_path / 'agent.db'))
    service.add_verified(project_id='p', kind='verified_build', value='Build passed',
        source_type='build_result', source_id='build-1', source_payload='ok',
        workspace_revision='old', affected_paths=('src/main.cpp',))
    service.invalidate_for_change('p', changed_paths=('README.md',), new_workspace_revision='new')
    assert len(service.active('p', current_workspace_revision='new')) == 1
    service.invalidate_for_change('p', changed_paths=('src/main.cpp',), new_workspace_revision='new2')
    assert not service.active('p', current_workspace_revision='new2')


def test_colored_windows_compiler_diagnostics_do_not_escape_project(tmp_path):
    assert contained_diagnostic_path(r'C:\outside\main.cpp', tmp_path) is None
    assert contained_diagnostic_path(r'\\server\share\main.cpp', tmp_path) is None
    compiler, _ = parse_build_diagnostics('\x1b[31msrc/main.cpp:5:2: error: missing ;\x1b[0m', tmp_path)
    assert compiler[0]['severity'] == 'error'
    assert compiler[0]['path'] == 'src/main.cpp'


def test_atomic_managed_file_rejects_stale_revision(tmp_path):
    async def scenario():
        service = ProjectService(tmp_path / 'projects')
        project = GeneratedProject(project_id='p123', project_name='safe-project',
            target_board='esp32dev', framework='PlatformIO',
            created_at=datetime.now(timezone.utc), files=(
                GeneratedFile('platformio.ini', '[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n', 'ini'),
                GeneratedFile('src/main.cpp', 'old\n', 'cpp'),
            ))
        await service.create_project(project)
        old_revision = hashlib.sha256(b'old\n').hexdigest()
        result = await service.save_file_if_revision_matches('safe-project', 'p123', 'src/main.cpp', 'new\n', old_revision)
        assert result.content == 'new\n'
        with pytest.raises(ProjectConflictError, match='FILE_REVISION_CONFLICT'):
            await service.save_file_if_revision_matches('safe-project', 'p123', 'src/main.cpp', 'stale\n', old_revision)
        saved = await service.load_project('safe-project')
        assert next(file.content for file in saved.files if file.path == 'src/main.cpp') == 'new\n'
    asyncio.run(scenario())


def test_build_operations_serialize_same_project_and_retain_both_records(tmp_path):
    async def scenario():
        store = AgentOrchestrationStore(tmp_path / 'agent.db')
        ops = BuildOperations(store)
        running = max_running = 0
        async def execute():
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            await asyncio.sleep(0.025)
            running -= 1
            return {'result': {'success': True}}, 'revision1'
        ops.accept('build-idempotency-0001', project_id='p', environment='dev', source_revision='revision1', execute=execute)
        ops.accept('build-idempotency-0002', project_id='p', environment='dev', source_revision='revision1', execute=execute)
        await asyncio.gather(*tuple(ops.tasks.values()))
        assert max_running == 1
        assert ops.get('build-idempotency-0001')['state'] == 'passed'
        assert ops.get('build-idempotency-0002')['state'] == 'passed'
    asyncio.run(scenario())


def test_remote_provider_credentials_require_https(tmp_path):
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / 'settings.json', credential_store=MemoryCredentialStore()))
    with pytest.raises(ValueError, match='HTTPS'):
        registry.configure_provider('openrouter', {'base_url':'http://example.com/v1', 'api_key':'secret'})
    with pytest.raises(ValueError, match='HTTPS'):
        registry.register_custom_provider(provider_id='unsafe_provider', display_name='No TLS', protocol='openai-completions',
            base_url='http://example.com/v1', default_model='model', models=['model'], api_key='secret')


def test_rollback_refuses_to_overwrite_later_manual_edit(tmp_path):
    from backend.services.chunked_generation_writer import WorkspaceGenerationTransaction, ChunkedGenerationWriter
    file = tmp_path / 'src' / 'main.cpp'
    file.parent.mkdir()
    file.write_text('original')
    transaction = WorkspaceGenerationTransaction(tmp_path)
    ChunkedGenerationWriter(tmp_path, transaction=transaction).write_file(GeneratedFile('src/main.cpp', 'generated'))
    file.write_text('newer human edit')
    with pytest.raises(ValueError, match='GENERATION_ROLLBACK_CONFLICT'):
        transaction.rollback()
    assert file.read_text() == 'newer human edit'

def test_rollback_restores_original_when_unmodified(tmp_path):
    from backend.services.chunked_generation_writer import WorkspaceGenerationTransaction, ChunkedGenerationWriter
    file = tmp_path / 'src' / 'main.cpp'
    file.parent.mkdir()
    file.write_text('original')
    transaction = WorkspaceGenerationTransaction(tmp_path)
    ChunkedGenerationWriter(tmp_path, transaction=transaction).write_file(GeneratedFile('src/main.cpp', 'generated'))
    assert transaction.rollback() is True
    assert file.read_text() == 'original'


def test_chunked_validation_honors_esp32_s3_board():
    from backend.services.generation_validators import validate_chunk_file
    config = '[env:s3]\nplatform = espressif32\nboard = esp32-s3-devkitc-1\nframework = arduino\n'
    assert validate_chunk_file('platformio.ini', config, forbidden_features=(),
        platformio_board='esp32-s3-devkitc-1') == ()
    assert any('board=esp32dev' in msg for msg in
        validate_chunk_file('platformio.ini', config, forbidden_features=(), platformio_board='esp32dev'))
