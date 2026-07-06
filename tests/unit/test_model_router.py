from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
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
from backend.model_router.providers.codex_agent import CodexAgentProvider
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
        return ProviderHealth(self.provider_id, not self.fail, "connected")

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

    assert provider_ids == ["openrouter", "openai", "groq", "gemini", "anthropic", "cerebras", "lmstudio", "ollama", "codex"]


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


def test_router_selects_codex_when_product_provider_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_CODEX_PROVIDER", "1")
    codex = FakeProvider("codex")
    router, _, _ = router_with_storage(tmp_path, {"codex": codex})

    response = run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "codex"
    assert codex.requests[0].model_id == "codex-agent"

    llm_response = run(router.generate(LLMRequest(prompt="hello", metadata={"task_type": "code_generation"})))
    assert llm_response.provider_id == "codex"


def test_local_only_code_generation_keeps_codex_candidate(tmp_path: Path) -> None:
    codex = FakeProvider("codex")
    router, _, _ = router_with_storage(tmp_path, {"codex": codex})

    response = run(router.generate_model(ModelRequest(
        prompt="hello",
        task_type="code_generation",
        provider_id="codex",
        model_id="codex-agent",
        local_only=True,
    )))

    assert response.provider_id == "codex"


@pytest.mark.asyncio
async def test_codex_generation_protocol_uses_disposable_working_directory(tmp_path: Path) -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.payloads: list[dict[str, object]] = []

        def write(self, value: bytes) -> None:
            self.payloads.append(json.loads(value))

        async def drain(self) -> None:
            return None

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = asyncio.StreamReader()
            for value in (
                {"id": 1, "result": {}},
                {"id": 2, "result": {"thread": {"id": "thread-001"}}},
                {"id": 3, "result": {"turn": {"id": "turn-001"}}},
                {"method": "item/completed", "params": {"item": {"id": "message-001", "type": "agentMessage", "phase": "final_answer", "text": "done"}}},
                {"method": "turn/completed", "params": {}},
            ):
                self.stdout.feed_data((json.dumps(value) + "\n").encode())
            self.stdout.feed_eof()

    isolated = tmp_path / "isolated-generation"
    isolated.mkdir()
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    provider = CodexAgentProvider(registry)
    process = FakeProcess()

    content = await provider._run_protocol(  # type: ignore[arg-type]
        process,
        ModelRequest(prompt="generate", task_type="code_generation"),
        cwd=isolated,
    )

    assert content == "done"
    assert process.stdin.payloads[2]["params"]["cwd"] == str(isolated.resolve())  # type: ignore[index]


def test_codex_generation_uses_bounded_one_shot_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    provider = CodexAgentProvider(registry)
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text("generated firmware", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    content = provider._run_exec(
        SimpleNamespace(path="codex.exe", prefix_args=()),
        ModelRequest(prompt="create firmware", system_prompt="return files", task_type="code_generation"),
        tmp_path,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:6] == ["codex.exe", "--ask-for-approval", "never", "exec", "--sandbox", "read-only"]
    assert command[-1] == "-"
    assert "create firmware" not in command
    assert captured["input"] == "return files\n\ncreate firmware"
    assert content == "generated firmware"


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

    response = run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "openai"
    records = usage.list_records()
    assert records[0]["success"] is False
    assert records[-1]["success"] is True


def test_router_skips_provider_with_known_unavailable_health(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", fail=True)
    openai = FakeProvider("openai", content="healthy fallback")
    router, registry, usage = router_with_storage(
        tmp_path,
        {"openrouter": openrouter, "openai": openai},
    )
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})
    registry.configure_provider("openai", {"api_key": "sk-openai", "enabled": True})
    registry.save_health("openai", {"status": "connected"})
    registry.save_health("openrouter", {"status": "offline", "message": "connection refused"})

    response = run(router.generate_model(ModelRequest(prompt="hello", task_type="code_generation")))

    assert response.provider_id == "openai"
    assert openrouter.requests == []
    assert [record["provider_id"] for record in usage.list_records()] == ["openai"]


def test_router_skips_unchecked_local_fallback(tmp_path: Path) -> None:
    openrouter = FakeProvider("openrouter", fail=True)
    ollama = FakeProvider("ollama", content="should not run")
    router, registry, _ = router_with_storage(tmp_path, {"openrouter": openrouter, "ollama": ollama})
    registry.configure_provider("openrouter", {"api_key": "sk-openrouter", "enabled": True})

    with pytest.raises(LLMProviderError, match="provider failed"):
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

    response = run(
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

    assert response.content == "fallback"
    assert openrouter.requests == []
    assert len(openai.requests) == 1


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
