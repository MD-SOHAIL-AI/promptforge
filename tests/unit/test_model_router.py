from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from backend.agent.planner import ExecutionPlan, ExecutionStep, Framework, TargetBoard, TaskType
from backend.contracts.execution_context import ExecutionContext
from backend.model_router import (
    ModelRequest,
    MemoryCredentialStore,
    ModelResponse,
    ModelRoute,
    ModelRouterService,
    ProviderRegistry,
    ProviderSettingsStorage,
    UsageTracker,
)
from backend.model_router.models import ProviderHealth
from backend.services.code_generation_service import CodeGenerationRequest, CodeGenerationService
from backend.services.llm_service import LLMConfigurationError, LLMProviderError, LLMRequest


@pytest.fixture(autouse=True)
def isolated_model_router_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model-router tests must not inherit feature flags loaded by API tests."""

    monkeypatch.delenv("PROMPTFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("PROMPTFORGE_MODEL", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)


class FakeProvider:
    def __init__(
        self,
        provider_id: str,
        content: str = "ok",
        *,
        fail: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.content = content
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.provider_id, True, "connected")

    async def list_models(self) -> list[object]:
        return []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider failed")
        return ModelResponse(
            content=self.content,
            provider_id=self.provider_id,
            model_id=request.model_id or "model",
            latency_ms=7,
        )


def run(value: Any) -> Any:
    return asyncio.run(value)


def router_with_storage(
    tmp_path: Path,
    providers: dict[str, FakeProvider],
) -> tuple[ModelRouterService, ProviderRegistry, UsageTracker]:
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    usage = UsageTracker(tmp_path / "usage.jsonl")
    router = ModelRouterService(registry, usage, providers)  # type: ignore[arg-type]
    return router, registry, usage


def test_provider_registry_lists_supported_providers(tmp_path: Path) -> None:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))

    provider_ids = [provider.provider_id for provider in registry.list_providers()]

    assert provider_ids == ["openrouter", "openai", "groq", "gemini", "anthropic", "cerebras", "nvidia_nim", "lmstudio", "ollama"]


def test_bridge_providers_are_not_generation_route_providers(tmp_path: Path) -> None:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))

    with pytest.raises(ValueError, match="Unknown model provider"):
        registry.save_route(
            ModelRoute(
                task_type="code_generation",
                provider_id="codex_bridge",
                model_id="codex",
            )
        )

    with pytest.raises(ValueError, match="Unknown model provider"):
        registry.save_route(
            ModelRoute(
                task_type="code_generation",
                provider_id="antigravity_cli_bridge",
                model_id="agy",
            )
        )


def test_router_selects_openrouter_for_code_generation_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)
    openrouter = FakeProvider("openrouter")
    router, registry, _ = router_with_storage(tmp_path, {"openrouter": openrouter})
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})

    response = run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "openrouter"
    assert openrouter.requests[0].model_id == "openai/gpt-oss-120b:free"
    assert len(router.decision_store.decisions) == 1
    decision = next(iter(router.decision_store.decisions.values()))
    assert decision.selected_provider_id == "openrouter" and decision.selected_model_id == response.model_id
    assert router.decision_store.usage[decision.decision_id]["success"] is True


def test_codex_feature_flag_cannot_insert_cli_into_model_routing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_CODEX_PROVIDER", "1")
    router, _, _ = router_with_storage(tmp_path, {"codex": FakeProvider("codex")})
    with pytest.raises(LLMConfigurationError):
        run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))


def test_explicit_codex_model_route_is_rejected(tmp_path: Path) -> None:
    router, _, _ = router_with_storage(tmp_path, {"codex": FakeProvider("codex")})
    with pytest.raises(ValueError, match="Unknown model provider"):
        run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation", provider_id="codex", model_id="codex-agent", local_only=True)))


def test_router_falls_back_when_provider_fails(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", fail=True)
    openai = FakeProvider("openai", content="fallback")
    router, registry, usage = router_with_storage(
        tmp_path,
        {"openrouter": openrouter, "openai": openai},
    )
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})
    registry.configure_provider("openai", {"api_key": "sk-openai", "enabled": True})
    registry.save_health("openai", {"status": "connected"})
    registry.save_route(ModelRoute("code_generation", "openrouter", registry.default_model("openrouter"), True, "openai"))

    response = run(router.generate_model(ModelRequest(
        prompt="hello",
        task_type="code_generation",
        allow_fallback=True,
        metadata={
            "fallback_policy": "ask_before_cross_provider",
            "consent_granted": True,
            "approved_recipients": ["openrouter", "openai"],
        },
    )))

    assert response.provider_id == "openai"
    records = usage.list_records()
    assert records[0]["success"] is False
    assert records[-1]["success"] is True


def test_router_does_not_fallback_from_known_unavailable_health_by_default(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", fail=True)
    openai = FakeProvider("openai", content="must not run")
    router, registry, usage = router_with_storage(
        tmp_path,
        {"openrouter": openrouter, "openai": openai},
    )
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})
    registry.configure_provider("openai", {"api_key": "sk-openai", "enabled": True})
    registry.save_health("openai", {"status": "connected"})
    registry.save_health("openrouter", {"status": "offline", "message": "unavailable"})

    with pytest.raises(LLMConfigurationError):
        run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert openrouter.requests == []
    assert openai.requests == []
    assert usage.list_records() == []


def test_router_skips_unchecked_local_fallback(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", fail=True)
    ollama = FakeProvider("ollama", content="should not run")
    router, registry, _ = router_with_storage(tmp_path, {"openrouter": openrouter, "ollama": ollama})
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})

    with pytest.raises(LLMProviderError, match="without an authorized fallback"):
        run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert ollama.requests == []


def test_unconfigured_provider_does_not_report_stale_health(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))
    registry.save_health("openrouter", {"status": "connected", "checked_at": "2026-01-01T00:00:00Z"})

    provider = registry.provider("openrouter")

    assert provider.configured is False
    assert provider.enabled is False
    assert provider.health_status == "not_configured"
    assert provider.last_checked_at is None


def test_router_llm_request_can_skip_primary_for_validation_fallback(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", content="primary")
    openai = FakeProvider("openai", content="fallback")
    router, registry, _ = router_with_storage(
        tmp_path,
        {"openrouter": openrouter, "openai": openai},
    )
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})
    registry.configure_provider("openai", {"api_key": "sk-openai", "enabled": True})
    registry.save_health("openai", {"status": "connected"})

    with pytest.raises(LLMConfigurationError) as captured:
        run(
            router.generate(
                LLMRequest(
                    prompt="hello",
                    metadata={
                        "task_type": "code_generation",
                        "skip_provider_id": "openrouter",
                        "force_fallback": True,
                    },
                )
            )
        )

    assert captured.value.code == "FALLBACK_NOT_AUTHORIZED"
    assert openrouter.requests == []
    assert openai.requests == []


def test_missing_api_key_returns_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PROMPTFORGE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)
    router, _, _ = router_with_storage(tmp_path, {"openrouter": FakeProvider("openrouter")})

    with pytest.raises(LLMConfigurationError, match="Add your OpenRouter API settings in Models"):
        run(
            router.generate_model(
                ModelRequest(
                    prompt="hello",
                    task_type="code_generation",
                    allow_fallback=False,
                )
            )
        )


def test_usage_tracking_records_success_and_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)
    failing = FakeProvider("openrouter", fail=True)
    router, registry, usage = router_with_storage(tmp_path, {"openrouter": failing})
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})

    with pytest.raises(Exception):
        run(
            router.generate_model(
                ModelRequest(
                    prompt="hello",
                    task_type="code_generation",
                    allow_fallback=False,
                )
            )
        )

    records = usage.list_records()
    assert len(records) == 1
    assert records[0]["provider_id"] == "openrouter"
    assert records[0]["success"] is False


def test_code_generation_service_can_use_model_router(tmp_path: Path) -> None:
    manifest = json.dumps(
        {
            "files": [
                {
                    "path": "platformio.ini",
                    "content": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
                },
                {
                    "path": "src/main.cpp",
                    "content": "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
                },
            ]
        }
    )
    router, registry, _ = router_with_storage(
        tmp_path,
        {"openrouter": FakeProvider("openrouter", content=manifest)},
    )
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})
    service = CodeGenerationService(router)
    plan = ExecutionPlan(
        task_id="task-router",
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
        requirements=("blink led",),
        execution_steps=(ExecutionStep.GENERATE_CODE,),
        confidence=0.9,
    )
    context = ExecutionContext(
        task_id="task-router",
        project_path="workspace/projects/router-demo",
        target_board="ESP32",
        framework="PlatformIO",
    )

    project = run(service.generate_project(CodeGenerationRequest(plan=plan, context=context)))

    assert project.project_name == "router-demo"
    assert project.list_files() == ("platformio.ini", "src/main.cpp")
