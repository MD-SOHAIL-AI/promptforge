"""Regressions for the October continuation: deadlines and source provenance."""
import asyncio
import hashlib
import time
from pathlib import Path

import pytest

from backend.model_router.models import ModelCapabilities, ModelRequest, RequestExecutionContext
from backend.model_router.resolver import ResolvedModel
from backend.model_router.runtime import ModelRuntime
from backend.model_router.retry import RetryPolicy
from backend.services.llm_service import LLMTimeoutError
from backend.runtime.result import BuildResult, ResultStatus
from backend.services.build_snapshot import isolated_build_sources, preserve_firmware
from backend.services.platformio_service import PlatformIOService
from backend.runtime.subprocess_mgr import SubprocessManager


def _project(root: Path) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "platformio.ini").write_text("[env:dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n")
    (root / "src" / "main.cpp").write_text("int main(){return 1;}")
    return root


def test_build_source_capture_is_an_independent_byte_copy(tmp_path):
    original = _project(tmp_path / "original")
    with isolated_build_sources(original) as snapshot:
        assert snapshot.files == 2
        assert snapshot.revision == snapshot.revision.lower()
        (original / "src" / "main.cpp").write_text("int main(){return 2;}")
        assert (snapshot.root / "src" / "main.cpp").read_text() == "int main(){return 1;}"
        assert snapshot.root != original
    assert not snapshot.root.exists()


def test_build_source_capture_rejects_linked_paths(tmp_path):
    original = _project(tmp_path / "original")
    try:
        (original / "src" / "linked.cpp").symlink_to(original / "src" / "main.cpp")
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation privilege is unavailable; junction coverage runs separately")
        raise
    with pytest.raises(ValueError, match="BUILD_SOURCE_SYMLINK_UNSUPPORTED"):
        with isolated_build_sources(original):
            pass


def test_build_source_capture_does_not_include_old_pio_artifacts(tmp_path):
    original = _project(tmp_path / "original")
    (original / ".pio").mkdir()
    (original / ".pio" / "firmware.bin").write_bytes(b"OLD")
    with isolated_build_sources(original) as snapshot:
        assert not (snapshot.root / ".pio").exists()


def test_build_snapshot_preserves_firmware_after_temporary_input_removed(tmp_path, monkeypatch):
    original = _project(tmp_path / "original")
    artifacts = tmp_path / "artifacts"
    service = PlatformIOService(SubprocessManager(), artifact_store=artifacts)
    observed = []

    async def fake_build(project, **kwargs):
        stage = Path(project)
        observed.append(stage)
        assert stage != original
        assert (stage / "src" / "main.cpp").read_text() == "int main(){return 1;}"
        # A live editor update during compilation cannot change this input.
        (original / "src" / "main.cpp").write_text("int main(){return 3;}")
        binary = stage / ".pio" / "build" / "dev" / "firmware.bin"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"VALID FIRMWARE")
        return BuildResult(success=True, status=ResultStatus.SUCCESS,
                           firmware_path=str(binary), message="compiled")

    monkeypatch.setattr(service, "_build_unlocked", fake_build)
    result = asyncio.run(service.build(original, environment="dev"))
    assert result.success and result.firmware_path
    assert Path(result.firmware_path).read_bytes() == b"VALID FIRMWARE"
    assert result.metadata["isolated_compilation"] is True
    assert result.metadata["firmware_sha256"] == hashlib.sha256(b"VALID FIRMWARE").hexdigest()
    assert not observed[0].exists()


def test_build_snapshot_output_persistence_failure_prevents_flash_ready(tmp_path, monkeypatch):
    original = _project(tmp_path / "original")
    service = PlatformIOService(SubprocessManager(), artifact_store=tmp_path / "store")

    async def fake_build(project, **kwargs):
        path = Path(project) / ".pio" / "build" / "dev" / "firmware.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"firmware")
        return BuildResult(True, ResultStatus.SUCCESS, firmware_path=str(path), metadata={"flash_ready": True})

    monkeypatch.setattr(service, "_build_unlocked", fake_build)
    monkeypatch.setattr("backend.services.build_snapshot.preserve_firmware", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    result = asyncio.run(service.build(original, environment="dev"))
    assert result.metadata["flash_ready"] is False
    assert result.metadata["artifact_persistence_failed"] is True
    assert result.firmware_path is None


def test_preserve_firmware_detects_tampered_existing_artifact(tmp_path):
    artifact = tmp_path / "firmware.bin"
    artifact.write_bytes(b"ok")
    path, sha = preserve_firmware(str(artifact), tmp_path / "store")
    Path(path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="BUILD_OUTPUT_STORE_CONFLICT"):
        preserve_firmware(str(artifact), tmp_path / "store")


def test_model_runtime_enforces_absolute_request_deadline():
    class Resolver:
        def resolve(self, request):
            return ResolvedModel("openai", "model", "openai-completions", ModelCapabilities())
    class HangingProvider:
        def __init__(self):
            self.cancelled = False
            self.requests = 0
        async def generate(self, request):
            self.requests += 1
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    async def check():
        provider = HangingProvider()
        runtime = ModelRuntime(Resolver(), {"openai": provider}, retry_policy=RetryPolicy(max_attempts=6))
        req = ModelRequest(prompt="Hello", execution_context=RequestExecutionContext(
            deadline=time.monotonic() + 0.03, managed=False))
        with pytest.raises(LLMTimeoutError) as result:
            await runtime.generate(req)
        assert result.value.code == "PROVIDER_REQUEST_DEADLINE_EXCEEDED"
        assert provider.cancelled is True
        assert provider.requests == 1
    asyncio.run(check())


def test_model_stream_deadline_closes_hanging_stream(tmp_path):
    from contextlib import aclosing
    from backend.model_router.router import ModelRouterService
    from backend.model_router.registry import ProviderRegistry
    from backend.model_router.storage import ProviderSettingsStorage
    from backend.model_router.credentials import MemoryCredentialStore
    from backend.model_router.usage import UsageTracker
    from backend.model_router.models import CanonicalStreamEvent, ModelRoute

    class WaitingStream:
        cancelled = False
        async def generate_event_stream(self, request):
            try:
                await asyncio.sleep(10)
                yield CanonicalStreamEvent(type="text_delta", text="late")
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def check():
        provider = WaitingStream()
        registry = ProviderRegistry(ProviderSettingsStorage(
            tmp_path / "providers.json", credential_store=MemoryCredentialStore()))
        registry.configure_provider("openai", {"api_key": "test-key", "enabled": True})
        registry.save_route(ModelRoute("code_generation", "openai", "gpt-4.1-mini"))
        router = ModelRouterService(registry=registry, usage=UsageTracker(tmp_path / "usage.jsonl"),
                                    providers={"openai": provider})
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def isolated_slot(_request):
            yield
        router._physical_slot = isolated_slot
        request = ModelRequest(prompt="hi", provider_id="openai", model_id="gpt-4.1-mini",
                               execution_context=RequestExecutionContext(
                                   deadline=time.monotonic()+0.04, managed=False))
        try:
            with pytest.raises(LLMTimeoutError, match="overall deadline"):
                async with aclosing(router.stream_model(request)) as stream:
                    async for _ in stream:
                        pass
            assert provider.cancelled
        finally:
            await router.aclose()

    asyncio.run(check())
