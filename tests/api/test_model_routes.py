from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.model_router import MemoryCredentialStore, ModelRequest, ModelResponse, ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.model_router.models import ModelInfo, ProviderHealth
from backend.services.code_generation_service import CodeGenerationService
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.llm_service import LLMProvider, LLMRequest, LLMResponse, LLMService
from backend.services.project_service import ProjectService


class FakeProvider:
    provider_id = "openrouter"

    def __init__(self, *, fail_models: bool = False, health_ok: bool = True) -> None:
        self.fail_models = fail_models
        self.health_ok = health_ok

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            "openrouter",
            self.health_ok,
            "connected" if self.health_ok else "error",
            latency_ms=1,
            error_code=None if self.health_ok else "AUTHENTICATION_ERROR",
            message=None if self.health_ok else "Invalid API key or insufficient credits",
        )

    async def list_models(self) -> list[ModelInfo]:
        if self.fail_models:
            raise RuntimeError("model endpoint unavailable")
        return [ModelInfo("openrouter", "openai/gpt-oss-120b:free", "gpt-oss-120b:free", free=True)]

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse("OK", "openrouter", request.model_id or "model", latency_ms=1)


class FakeLLM(LLMService):
    provider = LLMProvider.OPENROUTER
    model = "test-model"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse("OK", self.provider, self.model, latency_ms=1)

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        yield (await self.generate(request)).content

    async def health_check(self) -> bool:
        return True


def client(
    tmp_path: Path,
    provider: FakeProvider | None = None,
    code_generation_service: CodeGenerationService | None = None,
) -> TestClient:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))
    router = ModelRouterService(
        registry,
        UsageTracker(tmp_path / "usage.jsonl"),
        {"openrouter": provider or FakeProvider()},  # type: ignore[arg-type]
    )
    generation = code_generation_service or CodeGenerationService(
        router,
        diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"),
    )
    app = create_app(
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False)


def test_model_provider_routes_do_not_expose_keys(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        saved = api.post(
            "/models/providers/openrouter/configure",
            json={"api_key": "sk-test-secret-abcd", "enabled": True},
        )
        assert saved.status_code == 200
        assert "sk-test-secret-abcd" not in saved.text
        assert saved.json()["provider"]["credential_configured"] is True
        assert "api_key_masked" not in saved.json()["provider"]

        listed = api.get("/models/providers")
        assert listed.status_code == 200
        assert "sk-test-secret-abcd" not in listed.text
        assert listed.json()["providers"][0]["configured"] is True
        assert "health_status" in listed.json()["providers"][0]
        assert "models_cached" in listed.json()["providers"][0]


def test_model_routes_health_routes_and_usage(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        api.post(
            "/models/providers/openrouter/configure",
            json={"api_key": "sk-test-secret-abcd", "enabled": True},
        )
        route = api.post(
            "/models/routes",
            json={
                "task_type": "code_generation",
                "provider_id": "openrouter",
                "model_id": "openai/gpt-oss-120b:free",
            },
        )
        assert route.status_code == 200
        assert api.get("/models/routes").json()["routes"][0]["provider_id"] == "openrouter"

        health = api.post("/models/providers/openrouter/health")
        assert health.status_code == 200
        assert health.json()["health"]["ok"] is True

        tested = api.post("/models/test", json={"prompt": "ping", "provider_id": "openrouter"})
        assert tested.status_code == 200
        assert tested.json()["response"]["content"] == "OK"

        usage = api.get("/models/usage")
        assert usage.status_code == 200
        assert usage.json()["count"] == 1

        attempts = api.get("/models/generation-attempts")
        assert attempts.status_code == 200
        assert attempts.json() == {"attempts": [], "count": 0}


def test_model_health_caches_friendly_error(tmp_path: Path) -> None:
    with client(tmp_path, FakeProvider(health_ok=False)) as api:
        api.post(
            "/models/providers/openrouter/configure",
            json={"api_key": "sk-test-secret-abcd", "enabled": True},
        )
        health = api.post("/models/providers/openrouter/health")
        assert health.status_code == 200
        assert health.json()["health"]["message"] == "Invalid API key or insufficient credits"

        listed = api.get("/models/providers").json()["providers"][0]
        assert listed["health_status"] == "error"
        assert listed["last_error"] == "Provider authentication failed."
        assert listed["last_checked_at"]


def test_model_list_failure_returns_fallback_models(tmp_path: Path) -> None:
    with client(tmp_path, FakeProvider(fail_models=True)) as api:
        response = api.get("/models/providers/openrouter/models")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source"] == "fallback"
        assert payload["error"].startswith("Could not fetch model list")
        assert payload["models"]


def test_chunked_generation_runs_api_returns_persisted_runs(tmp_path: Path) -> None:
    store = GenerationDiagnosticsStore(tmp_path / "diagnostics")
    store.record_chunked_run(
        {
            "run_id": "run-api",
            "execution_id": "exec-api",
            "project_id": "project-api",
            "strategy": "chunked",
            "status": "success",
            "file_statuses": [{"path": "src/main.cpp", "status": "written"}],
        }
    )
    generation = CodeGenerationService(FakeLLM(), diagnostics_store=store)

    with client(tmp_path, code_generation_service=generation) as api:
        response = api.get("/models/chunked-generation-runs?execution_id=exec-api")
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert payload["runs"][0]["run_id"] == "run-api"

        cleared = api.post("/models/generation-diagnostics/clear")
        assert cleared.status_code == 200
        assert api.get("/models/chunked-generation-runs").json() == {"runs": [], "count": 0}
