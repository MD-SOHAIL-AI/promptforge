from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from backend.agent_runtime import ProductAgentService, ProductProviderRegistry
from backend.agent_runtime.product_agent_service import TERMINAL_AGENT_STATUSES
from backend.changes import ChangeSetService
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import ModelRoute
from backend.model_router.registry import ProviderRegistry
from backend.model_router.storage import ProviderSettingsStorage


def model_registry(tmp_path: Path) -> ProviderRegistry:
    storage = ProviderSettingsStorage(
        tmp_path / "model-settings.json",
        credential_store=MemoryCredentialStore(),
    )
    return ProviderRegistry(storage)


def change_service(tmp_path: Path) -> ChangeSetService:
    return ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")


def test_product_registry_has_only_canonical_and_local_test_providers(tmp_path: Path) -> None:
    registry = ProductProviderRegistry(model_provider_registry=model_registry(tmp_path), fake_enabled=False)
    ids = {item.provider_id for item in registry.list()}
    assert ids == {"deepseek", "anthropic", "openrouter", "openai", "groq", "cerebras", "nvidia_nim", "gemini", "ollama", "lmstudio"}
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        registry.resolve("verified_template")
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        registry.resolve("fake_planner")


def test_api_planner_uses_canonical_provider_settings_and_route(tmp_path: Path) -> None:
    models = model_registry(tmp_path)
    models.configure_provider(
        "openai",
        {
            "api_key": "sk-test-123456789",
            "enabled": True,
            "default_model": "gpt-test-default",
            "base_url": "https://gateway.example/v1",
        },
    )
    models.save_route(ModelRoute(
        task_type="code_generation",
        provider_id="openai",
        model_id="gpt-test-route",
        fallback_enabled=False,
        fallback_provider_id=None,
    ))
    registry = ProductProviderRegistry(model_provider_registry=models)
    entry = next(item for item in registry.list() if item.provider_id == "openai")
    assert entry.routeable is True
    assert entry.selected is True
    assert entry.model_id == "gpt-test-route"
    assert registry.default_provider_id() == "openai"
    planner = registry.resolve("openai")
    assert planner.provider_id == "openai"
    assert planner.model_id == "gpt-test-route"
    assert planner.transport_name == "model_router"


def test_unavailable_selected_provider_does_not_choose_another_recipient(tmp_path: Path) -> None:
    models = model_registry(tmp_path)
    models.configure_provider("openai", {"api_key": "test", "enabled": True, "default_model": "ready"})
    models.save_route(ModelRoute(task_type="code_generation", provider_id="anthropic", model_id="unavailable", fallback_enabled=False))
    registry = ProductProviderRegistry(model_provider_registry=models)
    assert any(entry.provider_id == "openai" and entry.routeable for entry in registry.list())
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        registry.default_provider_id()


def test_nvidia_nim_is_routeable_by_the_product_agent(tmp_path: Path) -> None:
    models = model_registry(tmp_path)
    models.configure_provider(
        "nvidia_nim",
        {"api_key": "nvapi-test", "enabled": True},
    )
    models.save_route(ModelRoute(
        task_type="code_generation",
        provider_id="nvidia_nim",
        model_id="nvidia/nemotron-3-super-120b-a12b",
        fallback_enabled=False,
        fallback_provider_id=None,
    ))

    registry = ProductProviderRegistry(model_provider_registry=models)
    planner = registry.resolve("nvidia_nim")

    assert planner.provider_id == "nvidia_nim"
    assert planner.model_id == "nvidia/nemotron-3-super-120b-a12b"
    assert planner.transport_name == "model_router"


def test_product_service_requires_orchestrator_for_enabled_runs(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        service = ProductAgentService(
            change_service=change_service(tmp_path),
            provider_registry=ProductProviderRegistry(model_provider_registry=model_registry(tmp_path), fake_enabled=True),
            enabled=True,
        )
        with pytest.raises(RuntimeError, match="AGENT_ORCHESTRATOR_REQUIRED"):
            await service.start_run(
                project_id="project", active_workspace_root=workspace, instruction="create file", provider_id="fake_planner"
            )
    asyncio.run(scenario())


class _Owner:
    def __init__(self) -> None:
        self.started = []
        self.cancelled = []
    async def start_run(self, **kwargs):
        self.started.append(kwargs)
        from backend.agent_runtime.product_agent_service import ProductAgentRun
        return ProductAgentRun("run-1", str(kwargs["project_id"]), str(kwargs["provider_id"]), model_id=kwargs.get("model_id"))
    def get_run(self, run_id):
        from backend.agent_runtime.product_agent_service import ProductAgentRun
        return ProductAgentRun(run_id, "p", "fake_planner")
    def events(self, run_id, **kwargs): return ()
    async def cancel(self, run_id): self.cancelled.append(run_id); return self.get_run(run_id)
    async def retry(self, run_id): return self.get_run(run_id)
    async def confirm_flash(self, run_id, **kwargs): return self.get_run(run_id)
    async def close(self): return None
    def steer(self, run_id, message): return self.get_run(run_id)
    def prepare_flash(self, run_id, **kwargs): return self.get_run(run_id)
    def apply_pending_changes(self, run_id): return self.get_run(run_id)
    def session_projection(self, session_id): return {}
    def transient_events(self, run_id, **kwargs): return ()
    def queued_guidance(self, run_id): return ()
    def edit_guidance(self, run_id, item_id, message): return {}
    def remove_guidance(self, run_id, item_id): return {}


def test_product_service_is_facade_and_ignores_legacy_fallback(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        service = ProductAgentService(
            change_service=change_service(tmp_path),
            provider_registry=ProductProviderRegistry(model_provider_registry=model_registry(tmp_path), fake_enabled=True),
            enabled=True,
        )
        owner = _Owner()
        service.attach_orchestrator(owner)
        run = await service.start_run(
            project_id="project", active_workspace_root=workspace, instruction="x",
            provider_id="fake_planner", model_id="m", fallback_provider_id="openai",
        )
        assert run.run_id == "run-1"
        assert owner.started[0]["fallback_provider_id"] is None
        await service.cancel(run.run_id)
        assert owner.cancelled == ["run-1"]
    asyncio.run(scenario())


def test_disabled_agent_runtime_rejects_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        service = ProductAgentService(
            change_service=change_service(tmp_path),
            provider_registry=ProductProviderRegistry(model_provider_registry=model_registry(tmp_path)),
            enabled=False,
        )
        with pytest.raises(PermissionError, match="AGENT_RUNTIME_DISABLED"):
            await service.start_run(project_id="project", active_workspace_root=workspace, instruction="safe")

    asyncio.run(scenario())
