from __future__ import annotations

import os

from pathlib import Path
from typing import AsyncIterator

from fastapi.testclient import TestClient
import httpx
import pytest

from backend.agent_runtime.session_store import AgentSessionStore
from backend.api.app import create_app
from backend.model_router import MemoryCredentialStore, ModelRequest, ModelResponse, ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.model_router.models import ModelInfo, ProviderHealth
from backend.model_router.providers.cerebras import CerebrasProvider
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
        local_api_token_override=None if os.getenv("FORGEX_LOCAL_TOKEN") else "t" * 43,
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        agent_session_store=AgentSessionStore(tmp_path / "sessions.sqlite3"),
        version="test-version",
    )
    headers = {} if os.getenv("FORGEX_LOCAL_TOKEN") else {"Authorization": f"Bearer {'t' * 43}"}
    return TestClient(app, base_url="http://127.0.0.1:8000", headers=headers, raise_server_exceptions=False)


def test_v2_selection_tracks_legacy_selector_and_reset(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        services = api.app.state.services
        store = services.agent_session_store
        session_id = store.create(kind="chat").session_id
        assert api.post("/models/providers/openrouter/configure", json={"api_key": "test", "enabled": True}).status_code == 200
        choice = {"provider_id": "openrouter", "model_id": "first", "session_id": session_id}
        assert api.put("/v2/models/session-selection", json=choice).status_code == 200
        assert store.get(session_id).model_id == "first"
        changed = api.patch(f"/v1/chat/sessions/{session_id}", json={"provider_id": "openrouter", "model_id": "legacy"})
        assert changed.status_code == 200, changed.text
        params = {"session_id": session_id}
        assert api.get("/v2/models/session-selection", params=params).json() == {"model": "openrouter/legacy", "source": "session"}
        bindings = api.get("/v2/models/role-bindings", params=params).json()["bindings"]
        assert all(value == {"model": "openrouter/legacy", "source": "session"} for value in bindings.values())
        assert services.model_router_service.selections.get(session_id=session_id).model_id == "legacy"
        assert api.delete("/v2/models/role-bindings/main", params=params).json() == {"removed": True}
        assert store.get(session_id).provider_id is None
        assert store.get(session_id).model_id is None
        restored = AgentSessionStore(tmp_path / "sessions.sqlite3").get(session_id)
        assert restored.provider_id is None and restored.model_id is None
        assert services.model_router_service.selections.get(session_id=session_id) is None
        assert api.get("/v2/models/session-selection", params=params).json() == {"model": None, "source": "task_route"}
        assert api.delete("/v2/models/role-bindings/main", params=params).json() == {"removed": False}
        # A legacy-only choice must also be reported as removed.
        store.configure(session_id, provider_id="openrouter", model_id="legacy-only")
        assert api.delete("/v2/models/role-bindings/main", params=params).json() == {"removed": True}


def test_v2_inheritance_and_legacy_reset(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        store = api.app.state.services.agent_session_store
        session_id = store.create(kind="chat").session_id
        params = {"session_id": session_id}
        assert api.post("/models/providers/openrouter/configure", json={"api_key": "test", "enabled": True}).status_code == 200
        for role, scope, model in [("main", None, "app"), ("main", session_id, "session"), ("review", None, "global-review"), ("review", session_id, "session-review")]:
            assert api.put("/v2/models/role-bindings", json={"provider_id": "openrouter", "model_id": model, "session_id": scope, "role": role}).status_code == 200
        def binding(role):
            return api.get("/v2/models/role-bindings", params=params).json()["bindings"][role]
        assert binding("review") == {"model": "openrouter/session-review", "source": "session_role"}
        assert api.delete("/v2/models/role-bindings/review", params=params).json()["removed"]
        assert store.get(session_id).model_id == "session"
        assert binding("review") == {"model": "openrouter/global-review", "source": "role"}
        assert binding("explore") == {"model": "openrouter/session", "source": "session"}
        store.clear_model_selection(session_id)
        assert binding("main") == {"model": "openrouter/app", "source": "application"}
        assert binding("review")["source"] == "role"


@pytest.mark.parametrize("session_id", ["", " ", "x" * 257, "../escape", "agent-session-missing"])
def test_v2_invalid_session_ids_cannot_access_application_scope(tmp_path: Path, session_id: str) -> None:
    from backend.model_router.selection import ModelRef
    with client(tmp_path) as api:
        selections = api.app.state.services.model_router_service.selections
        selections.set(ModelRef("openrouter", "preserved"))
        for path in ["session-selection", "role-bindings"]:
            assert api.get(f"/v2/models/{path}", params={"session_id": session_id}).status_code in (404, 422)
        assert api.put("/v2/models/session-selection", json={"provider_id": "openrouter", "model_id": "bad", "session_id": session_id}).status_code in (404, 422)
        for role in ["main", "review"]:
            assert api.delete(f"/v2/models/role-bindings/{role}", params={"session_id": session_id}).status_code in (404, 422)
            response = api.put("/v2/models/role-bindings", json={"provider_id": "openrouter", "model_id": "bad", "role": role, "session_id": session_id})
            assert response.status_code in (404, 422)
        assert selections.get() == ModelRef("openrouter", "preserved")


def test_v2_deleted_sessions_rejected_for_all_roles(tmp_path: Path) -> None:
    from backend.model_router.selection import ModelRef
    with client(tmp_path) as api:
        services = api.app.state.services
        identity = services.agent_session_store.create(kind="chat").session_id
        services.model_router_service.selections.set(ModelRef("openrouter", "stale"), session_id=identity, role="review")
        services.agent_session_store.delete(identity)
        for path in ["session-selection", "role-bindings"]:
            assert api.get(f"/v2/models/{path}", params={"session_id": identity}).status_code == 404
        for role in ["main", "review"]:
            assert api.delete(f"/v2/models/role-bindings/{role}", params={"session_id": identity}).status_code == 404
            assert api.put("/v2/models/role-bindings", json={"session_id": identity, "provider_id": "openrouter", "model_id": "new", "role": role}).status_code == 404


@pytest.mark.parametrize("provider,model", [("missing", "x"), ("openrouter", " "), (" ", "x")])
def test_v2_invalid_choices_preserve_both_stores(tmp_path: Path, provider: str, model: str) -> None:
    with client(tmp_path) as api:
        services = api.app.state.services
        identity = services.agent_session_store.create(kind="chat").session_id
        assert api.post("/models/providers/openrouter/configure", json={"api_key": "test", "enabled": True}).status_code == 200
        body = {"session_id": identity, "provider_id": "openrouter", "model_id": "preserved"}
        assert api.put("/v2/models/session-selection", json=body).status_code == 200
        assert api.put("/v2/models/session-selection", json={**body, "provider_id": provider, "model_id": model}).status_code == 422
        assert services.agent_session_store.get(identity).model_id == "preserved"
        assert services.model_router_service.selections.get(session_id=identity).model_id == "preserved"


@pytest.mark.parametrize("method,path", [
    ("GET", "/catalog"), ("GET", "/session-selection"),
    ("GET", "/role-bindings"), ("PUT", "/session-selection"),
    ("PUT", "/role-bindings"), ("DELETE", "/role-bindings/main"),
])
def test_v2_router_enforces_local_auth_without_global_middleware(method, path):
    from fastapi import FastAPI
    from backend.api.errors import install_error_handlers
    from backend.api.routes.model_gateway import router
    app = FastAPI()
    app.state.local_api_token = "test-local-token"
    app.include_router(router)
    install_error_handlers(app)
    with TestClient(app, base_url="http://127.0.0.1:8000") as api:
        kwargs = {"json": {"provider_id": "openrouter", "model_id": "model"}} if method == "PUT" else {}
        if method == "PUT" and path == "/role-bindings":
            kwargs["json"]["role"] = "main"
        denied = api.request(method, "/v2/models" + path, **kwargs)
        assert denied.status_code == 401
        assert denied.json()["code"] == "LOCAL_AUTH_REQUIRED"
        remote = api.request(method, "/v2/models" + path, headers={"Authorization": "Bearer test-local-token", "Origin": "https://untrusted.example"}, **kwargs)
        assert remote.status_code == 403
        allowed = api.request(method, "/v2/models" + path, headers={"Authorization": "Bearer test-local-token"}, **kwargs)
        # No services are installed: passing auth reaches the service boundary
        # rather than making a provider request.
        assert allowed.status_code == 503


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


def test_v2_model_role_selection_validate_persist_reset(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        configured = api.post("/models/providers/openrouter/configure", json={"api_key": "secret-test", "enabled": True})
        assert configured.status_code == 200
        selected = api.put("/v2/models/role-bindings", json={"provider_id": "openrouter", "model_id": "openai/gpt-oss-120b:free", "role": "review"})
        assert selected.status_code == 200, selected.text
        bindings = api.get("/v2/models/role-bindings")
        assert bindings.json()["bindings"]["review"]["model"] == "openrouter/openai/gpt-oss-120b:free"
        assert "secret-test" not in api.get("/v2/models/catalog").text
        invalid = api.put("/v2/models/role-bindings", json={"provider_id": "missing", "model_id": "x", "role": "review"})
        assert invalid.status_code == 422
        assert api.delete("/v2/models/role-bindings/review").json()["removed"] is True
        assert api.get("/v2/models/role-bindings").json()["bindings"]["review"]["model"] is None


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
        assert listed["last_error"] == "Invalid API key or insufficient credits"
        assert listed["last_checked_at"]


def test_model_list_failure_returns_fallback_models(tmp_path: Path) -> None:
    with client(tmp_path, FakeProvider(fail_models=True)) as api:
        response = api.get("/models/providers/openrouter/models")

        assert response.status_code == 200
        payload = response.json()
        assert payload["source"] == "fallback"
        assert payload["error"].startswith("Could not fetch model list")
        assert payload["models"]


def test_cerebras_model_endpoint_imports_account_models_and_reports_auth_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active_status = 200

    def respond(request: httpx.Request) -> httpx.Response:
        if active_status != 200:
            return httpx.Response(active_status)
        return httpx.Response(200, json={"data": [
            {"id": "gpt-oss-120b"}, {"id": "zai-glm-4.7"}, {"id": "private-model"},
        ]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    with client(tmp_path) as api:
        router = api.app.state.services.model_router_service
        router._providers["cerebras"] = CerebrasProvider(router.registry)
        configured = api.post("/models/providers/cerebras/configure", json={"api_key": "test-key"})
        assert configured.status_code == 200

        loaded = api.get("/models/providers/cerebras/models").json()
        assert loaded["source"] == "provider"
        assert [item["model_id"] for item in loaded["models"]] == [
            "gpt-oss-120b", "zai-glm-4.7", "private-model"
        ]

        active_status = 401
        failed = api.get("/models/providers/cerebras/models").json()
        assert failed["source"] == "fallback"
        assert "HTTP 401" in failed["error"]
        assert [item["model_id"] for item in failed["models"]] == [
            "gpt-oss-120b", "zai-glm-4.7", "private-model"
        ]


def test_custom_provider_lifecycle_and_delete_guards(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        created = api.post(
            "/models/providers",
            json={
                "provider_id": "lab_gateway",
                "display_name": "Lab Gateway",
                "protocol": "openai-responses",
                "base_url": "http://127.0.0.1:9000/v1",
                "default_model": "lab-model",
                "local": True,
            },
        )
        assert created.status_code == 201
        assert created.json()["provider"]["provider_id"] == "lab_gateway"
        assert any(item["provider_id"] == "lab_gateway" for item in api.get("/models/providers").json()["providers"])

        selected = api.post(
            "/models/routes",
            json={
                "task_type": "code_generation",
                "provider_id": "lab_gateway",
                "model_id": "lab-model",
                "local_only": True,
            },
        )
        assert selected.status_code == 200
        blocked = api.delete("/models/providers/lab_gateway")
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "MODEL_PROVIDER_IN_USE"


def test_custom_provider_can_be_deleted_when_unused(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        assert api.post(
            "/models/providers",
            json={
                "provider_id": "unused_gateway",
                "display_name": "Unused Gateway",
                "protocol": "openai-completions",
                "base_url": "http://127.0.0.1:9001/v1",
                "default_model": "model",
                "local": True,
            },
        ).status_code == 201
        deleted = api.delete("/models/providers/unused_gateway")
        assert deleted.status_code == 200
        assert deleted.json() == {"provider_id": "unused_gateway", "deleted": True}
        assert all(item["provider_id"] != "unused_gateway" for item in api.get("/models/providers").json()["providers"])


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
