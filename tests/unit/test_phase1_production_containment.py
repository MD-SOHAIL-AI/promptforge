from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent_runtime.coding_workflow_store import (
    LEGACY_WORKFLOW_ADMISSION_DISABLED,
    LEGACY_WORKFLOW_READ_ONLY,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)
from backend.api.app import create_app
from backend.model_router import (
    MemoryCredentialStore,
    ModelRequest,
    ModelResponse,
    ModelRoute,
    ModelRouterService,
    ProviderRegistry,
    ProviderSettingsStorage,
    UsageTracker,
)
from backend.model_router.models import ProviderHealth
from backend.services.llm_service import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
)
from backend.services.project_service import ProjectService


class RecordingProvider:
    def __init__(self, provider_id: str, *, error: Exception | None = None) -> None:
        self.provider_id = provider_id
        self.error = error
        self.requests: list[ModelRequest] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(self.provider_id, True, "connected")

    async def list_models(self) -> list[object]:
        return []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return ModelResponse("fallback", self.provider_id, request.model_id or "model")


def configured_router(tmp_path: Path, primary_error: Exception) -> tuple[ModelRouterService, RecordingProvider, RecordingProvider]:
    registry = ProviderRegistry(
        ProviderSettingsStorage(tmp_path / "providers.json", credential_store=MemoryCredentialStore())
    )
    primary = RecordingProvider("openrouter", error=primary_error)
    fallback = RecordingProvider("openai")
    router = ModelRouterService(
        registry,
        UsageTracker(tmp_path / "usage.jsonl"),
        {"openrouter": primary, "openai": fallback},  # type: ignore[arg-type]
    )
    registry.configure_provider("openrouter", {"api_key": "primary-test-key", "enabled": True})
    registry.configure_provider("openai", {"api_key": "fallback-test-key", "enabled": True})
    registry.save_health("openrouter", {"status": "connected"})
    registry.save_health("openai", {"status": "connected"})
    registry.save_route(
        ModelRoute(
            "code_generation",
            "openrouter",
            registry.default_model("openrouter"),
            True,
            "openai",
        )
    )
    return router, primary, fallback


def run(value):
    return asyncio.run(value)


def fallback_request(**metadata: object) -> ModelRequest:
    return ModelRequest(
        prompt="bounded test prompt",
        task_type="code_generation",
        allow_fallback=True,
        metadata=dict(metadata),
    )


def test_model_and_route_defaults_deny_fallback(tmp_path: Path) -> None:
    registry = ProviderRegistry(
        ProviderSettingsStorage(tmp_path / "providers.json", credential_store=MemoryCredentialStore())
    )
    assert ModelRequest(prompt="safe").allow_fallback is False
    assert ModelRoute("general_chat", "openrouter", "model").fallback_enabled is False
    assert registry.route_for_task("general_chat").fallback_enabled is False
    assert registry.route_for_task("general_chat").fallback_provider_id is None


def test_missing_consent_and_unproven_provider_group_block_fallback(tmp_path: Path) -> None:
    router, primary, fallback = configured_router(tmp_path, RuntimeError("provider unavailable"))
    with pytest.raises(LLMConfigurationError) as missing_consent:
        run(router.generate_model(fallback_request(
            fallback_policy="ask_before_cross_provider",
            approved_recipients=["openrouter", "openai"],
        )))
    assert missing_consent.value.code == "FALLBACK_NOT_AUTHORIZED"
    assert len(primary.requests) == 1 and fallback.requests == []

    router2, _, fallback2 = configured_router(tmp_path / "group", RuntimeError("provider unavailable"))
    with pytest.raises(LLMConfigurationError) as unproven_group:
        run(router2.generate_model(fallback_request(
            fallback_policy="approved_provider_group",
            consent_granted=True,
            approved_recipients=["openrouter", "openai"],
            approved_provider_groups=["trusted"],
        )))
    assert unproven_group.value.code == "FALLBACK_NOT_AUTHORIZED"
    assert fallback2.requests == []


def test_authentication_and_disclosure_failures_never_fallback(tmp_path: Path) -> None:
    auth_error = LLMAuthenticationError(
        "Authentication failed.",
        provider=LLMProvider.OPENROUTER,
        code="AUTHENTICATION_ERROR",
    )
    router, _, fallback = configured_router(tmp_path / "auth", auth_error)
    with pytest.raises(LLMAuthenticationError) as captured:
        run(router.generate_model(fallback_request(
            fallback_policy="ask_before_cross_provider",
            consent_granted=True,
            approved_recipients=["openrouter", "openai"],
        )))
    assert captured.value.code == "AUTHENTICATION_ERROR"
    assert fallback.requests == []

    disclosure_error = LLMProviderError(
        "Disclosure denied.",
        provider=LLMProvider.OPENROUTER,
        code="UNAPPROVED_EXTERNAL_DISCLOSURE",
    )
    router2, _, fallback2 = configured_router(tmp_path / "disclosure", disclosure_error)
    with pytest.raises(LLMProviderError) as captured2:
        run(router2.generate_model(fallback_request(
            fallback_policy="ask_before_cross_provider",
            consent_granted=True,
            approved_recipients=["openrouter", "openai"],
        )))
    assert captured2.value.code == "UNAPPROVED_EXTERNAL_DISCLOSURE"
    assert fallback2.requests == []


def test_missing_primary_credentials_never_uses_configured_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    registry = ProviderRegistry(
        ProviderSettingsStorage(tmp_path / "providers.json", credential_store=MemoryCredentialStore())
    )
    primary = RecordingProvider("openrouter")
    fallback = RecordingProvider("openai")
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {
        "openrouter": primary,
        "openai": fallback,
    })  # type: ignore[arg-type]
    registry.configure_provider("openai", {"api_key": "fallback-test-key", "enabled": True})
    registry.save_health("openai", {"status": "connected"})
    registry.save_route(ModelRoute("code_generation", "openrouter", "model", True, "openai"))

    with pytest.raises(LLMConfigurationError) as captured:
        run(router.generate_model(fallback_request(
            fallback_policy="ask_before_cross_provider",
            consent_granted=True,
            approved_recipients=["openrouter", "openai"],
        )))
    assert captured.value.code == "MISSING_CREDENTIALS"
    assert primary.requests == [] and fallback.requests == []


def legacy_run(run_id: str) -> CodingWorkflowRunRecord:
    return CodingWorkflowRunRecord(
        run_id=run_id,
        provider_id="fake_api_coding_agent",
        provider_type="api_coding_agent",
        status="completed",
        safe_message="Historical review is readable.",
    )


def test_production_jsonl_store_rejects_new_runs_but_reads_history(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    writer = CodingWorkflowStore.from_state_directory(root)
    writer.persist_run(
        legacy_run("historical-run"),
        (CodingWorkflowEventRecord(
            event_id="historical-event-1",
            run_id="historical-run",
            sequence=1,
            event_type="review.created",
            stage="generation",
            status="completed",
            safe_message="Historical review is readable.",
        ),),
    )
    before_runs = writer.runs_path.read_bytes()
    before_events = writer.events_path.read_bytes()
    reader = CodingWorkflowStore.from_state_directory(root, allow_new_runs=False, allow_mutations=False)

    assert reader.get_run("historical-run").safe_message == "Historical review is readable."
    assert reader.list_events("historical-run")[0].sequence == 1
    with pytest.raises(CodingWorkflowStoreError) as captured:
        reader.create_run(legacy_run("new-run"))
    assert captured.value.code == LEGACY_WORKFLOW_ADMISSION_DISABLED
    with pytest.raises(CodingWorkflowStoreError) as read_only:
        reader.append_event(CodingWorkflowEventRecord(
            event_id="historical-event-2",
            run_id="historical-run",
            sequence=2,
            event_type="workflow.cancelled",
            stage="workflow",
            status="cancelled",
            safe_message="Must not be written.",
        ))
    assert read_only.value.code == LEGACY_WORKFLOW_READ_ONLY
    with pytest.raises(CodingWorkflowStoreError) as operation_blocked:
        reader.acquire_operation_lock("historical-run", "apply")
    assert operation_blocked.value.code == LEGACY_WORKFLOW_READ_ONLY
    assert writer.runs_path.read_bytes() == before_runs
    assert writer.events_path.read_bytes() == before_events


def test_production_legacy_generation_route_fails_before_jsonl_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW", "1")
    monkeypatch.setenv("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "1")
    workspace = tmp_path / "active"
    workspace.mkdir()
    app = create_app(project_service=ProjectService(tmp_path / "projects"), version="phase1-test")
    store = app.state.coding_workflow_store
    assert store.allow_new_runs is False

    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post(
            "/models/coding-workflow/fake/generate-review",
            json={"prompt": "safe", "workspace_path": str(workspace)},
        )
    assert response.status_code == 503
    assert response.json() == {
        "code": "LEGACY_WORKFLOW_ADMISSION_DISABLED",
        "message": "New legacy workflow admission is disabled; use the durable Agent Workspace.",
        "details": {},
    }
    assert store.list_runs() == ()
    assert not store.runs_path.exists()
    assert not store.events_path.exists()

def test_production_historical_jsonl_routes_are_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root-read-only"))
    monkeypatch.setenv("FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW", "1")
    app = create_app(project_service=ProjectService(tmp_path / "projects-read-only"), version="phase1-test")
    store = app.state.coding_workflow_store
    assert store.allow_new_runs is False
    assert store.allow_mutations is False
    writer = CodingWorkflowStore(runs_path=store.runs_path, events_path=store.events_path)
    writer.persist_run(legacy_run("historical-run"), ())
    before_runs = store.runs_path.read_bytes()
    before_events = store.events_path.read_bytes() if store.events_path.exists() else None

    with TestClient(app, raise_server_exceptions=False) as api:
        read_response = api.get("/models/coding-workflow/historical-run")
        mutation_response = api.post(
            "/models/coding-workflow/historical-run/cancel",
            json={"cancel_confirmed": True, "reason": "must stay read-only"},
        )

    assert read_response.status_code == 200
    assert mutation_response.status_code == 503
    assert mutation_response.json() == {
        "code": "LEGACY_WORKFLOW_READ_ONLY",
        "message": "Historical legacy workflows are read-only; use the durable Agent Workspace.",
        "details": {},
    }
    assert store.runs_path.read_bytes() == before_runs
    assert (store.events_path.read_bytes() if store.events_path.exists() else None) == before_events
