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
    assert ids == {"verified_template", "fake_planner", "openrouter", "openai", "groq", "gemini"}
    assert registry.resolve("verified_template").provider_id == "verified_template"
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
    assert planner.endpoint == "https://gateway.example/v1/chat/completions"


def test_fake_agent_run_creates_changeset_and_leaves_workspace_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "README.txt").write_text("baseline\n", encoding="utf-8")
        changes = change_service(tmp_path)
        providers = ProductProviderRegistry(model_provider_registry=model_registry(tmp_path), fake_enabled=True)
        service = ProductAgentService(change_service=changes, provider_registry=providers, enabled=True)
        try:
            run = await service.start_run(
                project_id="project",
                active_workspace_root=workspace,
                instruction="create a safe test file",
                provider_id="fake_planner",
            )
            for _ in range(200):
                completed = service.get_run(run.run_id)
                if completed.status in TERMINAL_AGENT_STATUSES:
                    break
                await asyncio.sleep(0.01)
            completed = service.get_run(run.run_id)
            assert completed.status == "completed"
            assert completed.change_set_id
            assert completed.created_file_count == 1
            assert completed.active_workspace_unchanged is True
            assert not (workspace / "FORGEX_AGENT_RUNTIME_SMOKE.txt").exists()
            staged = changes.get(completed.change_set_id)
            assert staged.status == "pending"
            safe = completed.to_safe_dict()
            assert safe["change_set_id"] == completed.change_set_id
            assert "raw_prompt" not in str(safe).casefold()
        finally:
            await service.close()

    asyncio.run(scenario())


def test_verified_template_agent_is_routeable_without_api_key(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = change_service(tmp_path)
        providers = ProductProviderRegistry(model_provider_registry=model_registry(tmp_path))
        service = ProductAgentService(change_service=changes, provider_registry=providers, enabled=True)
        try:
            run = await service.start_run(
                project_id="project",
                active_workspace_root=workspace,
                instruction="Create an ESP32 blink project",
                provider_id="verified_template",
            )
            for _ in range(200):
                current = service.get_run(run.run_id)
                if current.status in TERMINAL_AGENT_STATUSES:
                    break
                await asyncio.sleep(0.01)
            current = service.get_run(run.run_id)
            assert current.status == "completed"
            assert current.change_set_id
            assert current.created_file_count >= 2
            assert not (workspace / "platformio.ini").exists()
        finally:
            await service.close()

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


def test_local_greeting_does_not_create_changeset(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = change_service(tmp_path)
        service = ProductAgentService(
            change_service=changes,
            provider_registry=ProductProviderRegistry(model_provider_registry=model_registry(tmp_path)),
            enabled=True,
        )
        run = await service.start_run(project_id="project", active_workspace_root=workspace, instruction="hello", provider_id="verified_template")
        assert run.status == "completed"
        assert run.classification == "LOCAL_RESPONSE"
        assert run.change_set_id is None
        assert changes.list() == ()
        await service.close()

    asyncio.run(scenario())


class SlowPlanner:
    provider_id = "slow"
    model_id = "test"

    def request_turn(self, **kwargs):
        del kwargs
        time.sleep(0.1)
        from backend.agent_runtime.product_providers import FakeProductPlanner
        return FakeProductPlanner().request_turn(task="x", tool_results=(), allowed_tools=("write_file",), run_id="x", turn=1)


class SlowRegistry:
    def resolve(self, provider_id: str):
        assert provider_id == "slow"
        return SlowPlanner()


def test_product_service_cancel_stops_before_apply(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = change_service(tmp_path)
        service = ProductAgentService(
            change_service=changes,
            provider_registry=SlowRegistry(),  # type: ignore[arg-type]
            enabled=True,
        )
        try:
            run = await service.start_run(project_id="project", active_workspace_root=workspace, instruction="cancel", provider_id="slow")
            await service.cancel(run.run_id)
            for _ in range(200):
                current = service.get_run(run.run_id)
                if current.status in TERMINAL_AGENT_STATUSES:
                    break
                await asyncio.sleep(0.01)
            current = service.get_run(run.run_id)
            assert current.status == "cancelled"
            assert current.change_set_id is None
            assert changes.list() == ()
        finally:
            await service.close()

    asyncio.run(scenario())
