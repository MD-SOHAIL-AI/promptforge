"""Integration regressions for the complete October 20 review."""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.api.dependencies import resolve_project
from backend.contracts.transport import TransportPolicy
from backend.model_router.models import ModelCapabilities, ModelRequest, RequestExecutionContext
from backend.model_router.resolver import ResolvedModel
from backend.model_router.runtime import ModelRuntime
from backend.runtime.result import BuildResult, ResultStatus
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.build_snapshot import isolated_build_sources, preserve_firmware, verify_build_image
from backend.services.llm_service import LLMTimeoutError
from backend.services.platformio_service import PlatformIOService
from backend.services.project_service import ProjectService
from tests.unit.test_2026_production_hardening import sample_project


def firmware_project(root):
    (root / 'src').mkdir(parents=True)
    (root / 'platformio.ini').write_text('[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n')
    (root / 'src/main.cpp').write_text('void setup(){} void loop(){}')
    return root


async def build_agent(root, monkeypatch):
    workspace = firmware_project(root / 'stage')
    service = PlatformIOService(SubprocessManager(), artifact_store=root / 'artifacts')
    async def compile_snapshot(project, **kwargs):
        output = Path(project) / '.pio/build/esp32dev'
        output.mkdir(parents=True)
        (output / 'firmware.bin').write_bytes(b'verified compiler fixture')
        (output / 'firmware.elf').write_bytes(b'verified ELF fixture')
        return BuildResult(success=True, status=ResultStatus.SUCCESS,
            firmware_path=str(output / 'firmware.bin'), board='esp32dev',
            message='compiled', metadata={'environment': 'esp32dev'})
    monkeypatch.setattr(service, '_build_unlocked', compile_snapshot)
    async def progress(*args): pass
    loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=object(),
        platformio=service, progress=progress, subprocess_manager=SubprocessManager())
    async def preflight(*args, **kwargs): return None
    monkeypatch.setattr(loop, '_preflight_dependencies', preflight)
    await loop._build_once('esp32dev')
    return loop, service


def test_agent_accepts_durable_compiler_output_and_rechecks_tampering(tmp_path, monkeypatch):
    async def check():
        loop, service = await build_agent(tmp_path, monkeypatch)
        assert loop.build_passed and loop.last_build['success']
        assert loop._refresh_verification()
        binary = Path(loop.last_build['firmware_path'])
        assert binary.is_relative_to(service.artifact_store)
        assert not binary.is_relative_to(loop.workspace_root)
        assert Path(loop.last_build['elf_path']).read_bytes() == b'verified ELF fixture'
        assert loop.last_build['source_snapshot_sha256']
        binary.write_bytes(b'tampered after compile')
        assert not loop._refresh_verification()
        assert not loop.build_passed
    asyncio.run(check())


def test_durable_build_invalidation_still_checks_source_revision(tmp_path, monkeypatch):
    async def check():
        loop, _ = await build_agent(tmp_path, monkeypatch)
        (loop.workspace_root / 'src/main.cpp').write_text('changed after compile')
        assert not loop._refresh_verification()
    asyncio.run(check())


def test_edit_and_revert_around_capture_cannot_verify_the_wrong_source(tmp_path, monkeypatch):
    original_build = PlatformIOService.build
    async def changed_build(service, project, **kwargs):
        source = Path(project) / 'src/main.cpp'
        original = source.read_bytes()
        source.write_text('different source captured during queue wait')
        try:
            return await original_build(service, project, **kwargs)
        finally:
            source.write_bytes(original)
    monkeypatch.setattr(PlatformIOService, 'build', changed_build)
    async def check():
        loop, _ = await build_agent(tmp_path, monkeypatch)
        assert not loop.build_passed
        assert loop.last_build['error_code'] == 'VERIFICATION_STALE'
    asyncio.run(check())


def test_persisted_image_does_not_authorize_an_unrelated_external_file(tmp_path):
    workspace = firmware_project(tmp_path / 'stage')
    arbitrary = tmp_path / 'firmware.bin'
    arbitrary.write_bytes(b'outside')
    store = tmp_path / 'store'
    saved, digest = preserve_firmware(str(arbitrary), store)
    assert verify_build_image(saved, workspace_root=workspace, artifact_store=store,
        expected_sha256=digest)[1] == digest
    with pytest.raises(ValueError):
        verify_build_image(arbitrary, workspace_root=workspace, artifact_store=store)
    with pytest.raises(ValueError):
        verify_build_image(saved, workspace_root=workspace)
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        verify_build_image(saved, workspace_root=workspace, artifact_store=store, expected_sha256='0' * 64)


def test_simulation_uses_staged_project_with_durable_images(tmp_path, monkeypatch):
    async def check():
        loop, service = await build_agent(tmp_path, monkeypatch)
        captured = {}
        async def simulate(self, artifact, board, config, **kwargs):
            captured.update(kwargs, environment=artifact.environment, firmware=artifact.path)
            return SimpleNamespace(success=True, status=ResultStatus.SUCCESS, message='simulated',
                simulation_artifact=None, board='esp32dev', serial_output=[], lines_captured=0,
                output_truncated=False, simulation_duration_ms=1, failure=None)
        monkeypatch.setattr('backend.agent_runtime.autonomous_loop.WokwiSimulator.simulate', simulate)
        result = await loop._simulate(1, call_id='simulation')
        assert result['output']['success']
        assert captured['source_project'] == loop.workspace_root
        assert captured['environment'] == 'esp32dev'
        assert captured['firmware'].is_relative_to(service.artifact_store)
        assert Path(captured['elf_path']).is_file()
    asyncio.run(check())


def test_listing_and_id_resolution_recover_an_interrupted_project_save(tmp_path):
    async def check():
        root = tmp_path / 'projects'
        service = ProjectService(root)
        await service.create_project(sample_project())
        (root / 'firmware').replace(root / '.firmware.backup')
        fresh = ProjectService(root)
        project = await resolve_project(fresh, 'workspace-id')
        assert Path(project.project_path) == root / 'firmware'
        assert len(await fresh.list_projects()) == 1
        assert not (root / '.firmware.backup').exists()
    asyncio.run(check())


def test_discovery_does_not_publish_an_invalid_backup(tmp_path):
    async def check():
        root = tmp_path / 'projects'
        service = ProjectService(root)
        await service.create_project(sample_project())
        (root / 'firmware').replace(root / '.firmware.backup')
        (root / '.firmware.backup/src/main.cpp').unlink()
        with pytest.raises(ValueError):
            await ProjectService(root).list_projects()
        assert not (root / 'firmware').exists()
        assert (root / '.firmware.backup').exists()
    asyncio.run(check())


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction behavior')
def test_snapshot_rejects_a_windows_junction(tmp_path):
    project = firmware_project(tmp_path / 'project')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'external.cpp').write_text('outside project')
    junction = project / 'linked'
    result = subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(outside)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    try:
        assert junction.is_junction() and not junction.is_symlink()
        with pytest.raises(ValueError, match='BUILD_SOURCE_SYMLINK_UNSUPPORTED'):
            with isolated_build_sources(project): pass
    finally:
        # Remove only the junction, never its target or a recursive tree.
        os.rmdir(junction)
    assert (outside / 'external.cpp').read_text() == 'outside project'


@pytest.mark.parametrize('parent_seconds,total_seconds', [(5, .05), (.05, 5)])
def test_logical_request_uses_the_earlier_deadline(parent_seconds, total_seconds):
    class Resolver:
        def resolve(self, request):
            return ResolvedModel('openai', 'model', 'openai-completions', ModelCapabilities())
    class Provider:
        cancelled = False
        async def generate(self, request):
            try: await asyncio.Event().wait()
            finally: self.cancelled = True
    async def check():
        provider = Provider()
        loop = asyncio.get_running_loop()
        request = ModelRequest(prompt='offline', execution_context=RequestExecutionContext(
            deadline=loop.time() + parent_seconds, managed=False,
            transport_policy=TransportPolicy(total_seconds=total_seconds)))
        with pytest.raises(LLMTimeoutError) as failure:
            await asyncio.wait_for(ModelRuntime(Resolver(), {'openai': provider}).generate(request), 1)
        assert failure.value.code == 'PROVIDER_REQUEST_DEADLINE_EXCEEDED'
        assert provider.cancelled
    asyncio.run(check())


def test_stream_total_deadline_precedes_later_parent_deadline(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager, aclosing
    from backend.model_router.registry import ProviderRegistry
    from backend.model_router.storage import ProviderSettingsStorage
    from backend.model_router.credentials import MemoryCredentialStore
    from backend.model_router.router import ModelRouterService
    from backend.model_router.usage import UsageTracker
    class Provider:
        cancelled = False
        async def generate_event_stream(self, request):
            try:
                await asyncio.Event().wait()
                yield None
            finally: self.cancelled = True
    async def check():
        provider = Provider()
        registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / 'settings.json',
            credential_store=MemoryCredentialStore()))
        registry.configure_provider('openai', {'api_key': 'fixture', 'enabled': True})
        router = ModelRouterService(registry, UsageTracker(tmp_path / 'usage.jsonl'), providers={'openai': provider})
        @asynccontextmanager
        async def slot(request): yield
        monkeypatch.setattr(router, '_physical_slot', slot)
        request = ModelRequest(prompt='offline', provider_id='openai', model_id='gpt-4.1-mini',
            execution_context=RequestExecutionContext(managed=False,
                deadline=asyncio.get_running_loop().time() + 5,
                transport_policy=TransportPolicy(total_seconds=.05)))
        async def consume():
            async with aclosing(router.stream_model(request)) as stream:
                async for _ in stream: pass
        try:
            with pytest.raises(LLMTimeoutError): await asyncio.wait_for(consume(), 1)
            assert provider.cancelled
        finally: await router.aclose()
    asyncio.run(check())
