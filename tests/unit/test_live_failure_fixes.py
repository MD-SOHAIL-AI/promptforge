"""Regressions for failures captured in the October 6 desktop session."""
import asyncio
from pathlib import Path

import pytest

from backend.agent_runtime.failure_contract import failure_details
from backend.agent_runtime.task_contract import create_task_contract, repair_task_contract
from backend.model_router.models import ModelRequest, ModelRoute, RequestExecutionContext
from backend.model_router.models import ModelResponse
from backend.model_router.router import ModelRouterService
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router.resolver import ModelResolver
from backend.model_router.sqlite_storage import SQLiteProviderSettingsStorage
from backend.model_router.storage import ProviderSettingsStorage
from backend.model_router.registry import ProviderRegistry
from backend.model_router.credentials import MemoryCredentialStore
from backend.services.llm_service import LLMError
from tests.unit.test_opencode_engine import registry, upstream

RETIRED = "qwen/qwen3-coder-480b-a35b-instruct"
SPEC = ("Build a complete ESP32 room monitor using PlatformIO. Provide organized source files, "
        "wiring documentation and README.md. Compile the firmware. Do not flash or open serial.")


def test_mixed_paragraph_keeps_requirements_and_build_acceptance(tmp_path):
    contract = create_task_contract(SPEC, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    positive = "\n".join(item["text"] for item in contract["requirements"])
    assert "room monitor" in positive and "README.md" in positive and "Compile" in positive
    assert "Do not flash" not in positive
    assert contract["exclusions"] == ["Do not flash or open serial."]
    assert any(item["kind"] == "build" for item in contract["acceptance_checks"])
    assert {item["kind"] for item in contract["deliverables"]} >= {"firmware_source", "documentation"}


def test_old_contract_repair_preserves_ids_exclusions_and_history(tmp_path):
    contract = create_task_contract("Keep modules small.", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    contract.pop("partition_version")
    contract["exclusions"] = [SPEC, "Future extensions", "Add remote cloud control."]
    original_revision = contract["revision"]
    repaired = repair_task_contract(contract, workspace=tmp_path)
    assert repaired["requirements"][0] == contract["requirements"][0]
    assert repaired["requirements"][1]["id"] == "req-2"
    assert "README.md" in "\n".join(item["text"] for item in repaired["requirements"])
    assert "Add remote cloud control." in repaired["exclusions"]
    assert all("remote cloud" not in item["text"] for item in repaired["requirements"])
    # Continuation upgrades can record intermediate contract revisions.
    assert repaired["parent_revision"] == repaired["revision_history"][-2]
    assert repaired["revision_history"][0] == original_revision
    assert contract["exclusions"][0] == SPEC  # Historical input is unchanged.
    assert repair_task_contract(repaired, workspace=tmp_path) == repaired


@pytest.mark.parametrize("store_type", [ProviderSettingsStorage, SQLiteProviderSettingsStorage])
def test_model_retirement_is_durable_and_scoped_to_endpoint(tmp_path, store_type):
    path = tmp_path / "settings"
    credentials = MemoryCredentialStore()
    reg = ProviderRegistry(store_type(path, credential_store=credentials))
    reg.mark_model_retired("deepseek", "retired-model")
    reg = ProviderRegistry(store_type(path, credential_store=credentials))
    assert reg.model_availability("deepseek", "retired-model")["availability"] == "retired"
    reg.configure_provider("deepseek", {"base_url": "http://127.0.0.1:9999/v1"})
    assert reg.model_availability("deepseek", "retired-model") == {}


def test_known_hosted_retired_model_is_rejected_without_a_request(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("nvidia_nim", {"api_key": "fixture", "enabled": True})
    with pytest.raises(LLMError) as failure:
        ModelResolver(reg).resolve(ModelRequest(prompt="continue", provider_id="nvidia_nim", model_id=RETIRED))
    assert failure.value.code == "MODEL_RETIRED"
    with pytest.raises(ValueError, match="MODEL_RETIRED"):
        reg.save_route(ModelRoute("code_generation", "nvidia_nim", RETIRED))
    # The public model's retirement does not prohibit a self-hosted NIM.
    reg.configure_provider("nvidia_nim", {"base_url": "http://127.0.0.1:9999/v1"})
    assert reg.model_availability("nvidia_nim", RETIRED) == {}


def test_nim_native_generation_has_idle_grace_without_changing_review(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("nvidia_nim", {"api_key": "fixture"})
    model = "nvidia/nemotron-3-super-120b-a12b"
    assert reg.model_capabilities("nvidia_nim", model).native_tools
    class Capture:
        requests = []
        async def generate(self, request):
            self.requests.append(request)
            return ModelResponse(content="", provider_id="nvidia_nim", model_id=model,
                finish_reason="tool_calls", tool_calls=({"id": "read-1", "name": "read",
                    "arguments": {"path": "platformio.ini"}},))
    adapter = Capture()
    router = ModelRouterService(registry=reg, providers={"nvidia_nim": adapter})
    for phase in ("generation", "review"):
        planner = RouterProductPlanner(router, provider_id="nvidia_nim", model_id=model)
        planner.bind_phase(phase)
        planner.request_turn(task="Inspect configuration", tool_results=(), allowed_tools=("read_file",), run_id="scope", turn=1)
    from backend.contracts.transport import TransportPolicy
    generation = adapter.requests[0].execution_context
    review = adapter.requests[1].execution_context
    assert generation.transport_policy.idle_seconds == 120
    assert generation.deadline is not None
    assert (review.transport_policy or TransportPolicy()).idle_seconds == 60


def test_model_catalog_metadata_survives_sqlite_restart(tmp_path):
    path = tmp_path / "models.sqlite3"
    store = SQLiteProviderSettingsStorage(path, credential_store=MemoryCredentialStore())
    store.save_model_catalog_metadata("nvidia_nim", {"source": "provider", "checked_at": "observed"})
    restored = SQLiteProviderSettingsStorage(path, credential_store=MemoryCredentialStore())
    assert restored.model_catalog_metadata("nvidia_nim") == {"source": "provider", "checked_at": "observed"}


def test_real_worker_410_is_permanent_and_remembered(tmp_path):
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        handler.send_response(410)
        handler.send_header("Content-Type", "application/json")
        handler.end_headers()
        handler.wfile.write(b'{"error":{"message":"Model is retired and no longer available"}}')
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "fixture", "base_url": url})
        context = RequestExecutionContext()
        request = ModelRequest(prompt="continue", provider_id="deepseek", model_id="test-model", execution_context=context)
        with pytest.raises(LLMError) as failure:
            asyncio.run(OpenCodeProvider(reg, "deepseek").generate(request))
        assert failure.value.code == "MODEL_RETIRED" and failure.value.status_code == 410
        assert failure.value.retryable is False
        assert failure.value.details["origin"] == "provider_model"
        assert context.attempts["used"] == 1
        with pytest.raises(LLMError, match="retired"):
            asyncio.run(OpenCodeProvider(reg, "deepseek").generate(request))
    assert len(bodies) == 1
    detail = failure_details("MODEL_RETIRED", status="failed", actions=["resume", "change_model"])
    assert detail["category"] == "model_unavailable" and detail["origin"] == "provider"
    assert detail["retryable"] is False
