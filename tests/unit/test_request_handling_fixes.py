"""Regressions for malformed verification calls and blocked catalog requests."""
import asyncio
import json
import threading
import time

import pytest
from pydantic import ValidationError

from backend.agent_runtime.lifecycle import turn_allowance
from backend.agent_runtime.session_store import AgentSessionStore
from backend.agent_runtime.tool_contracts import ProductToolCall
from backend.api.routes.agent_runtime import AgentRuntimeStartRequest, AgentSessionMessageRequest
from backend.api.routes.chat import ChatMessage
from backend.contracts.runtime_policy import MAX_AGENT_MESSAGE_CHARACTERS
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import ModelInfo
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.sqlite_storage import SQLiteProviderSettingsStorage

CHECKS = [{"id": "led", "status": "implemented", "evidence": [
    {"path": "src/main.cpp", "quote": "digitalWrite(LED_PIN, HIGH);"}]}]


def verify_call(checks):
    return ProductToolCall.parse({"id": "verify", "type": "verify_requirements", "arguments": {"checks": checks}})


def test_encoded_verification_checks_are_normalized_without_changing_source_evidence():
    original = json.dumps(CHECKS)
    call = verify_call(original)
    assert call.arguments["checks"] == CHECKS
    assert call.as_runtime_call().arguments["checks"] == CHECKS
    assert original == json.dumps(CHECKS)
    assert verify_call(CHECKS).arguments == call.arguments


@pytest.mark.parametrize("checks", [
    "not JSON", "{}", "null", "[]", json.dumps(json.dumps(CHECKS)),
    '[{"id":"led","status":"implemented","status":"unmet","evidence":[]}]',
    '[{"id":"led","status":"implemented","evidence":[],"extra":NaN}]',
    '[{"id":"led","status":"verified","evidence":[]}]',
    '[{"id":"led","status":"implemented","evidence":"[]"}]',
    '[{"id":"led","status":"implemented","evidence":[{"path":"src/main.cpp"}]}]',
    json.dumps(CHECKS * 257), " " * 524289, "\ud800",
], ids=["plain-text", "object", "null", "empty", "double-encoded", "duplicate-key", "nonfinite",
        "invalid-status", "encoded-nested-evidence", "missing-quote", "too-many-checks", "too-large", "surrogate"])
def test_encoded_checks_still_require_valid_bounded_evidence(checks):
    with pytest.raises(ValueError, match="product_tool_arguments_invalid"):
        verify_call(checks)


def test_json_looking_file_content_is_preserved():
    value = ProductToolCall.parse({"id": "write", "type": "write_file", "arguments": {
        "path": "data/config.json", "content": '[{"checks":"[]"}]'}})
    assert value.content == '[{"checks":"[]"}]'


def test_generation_uses_remaining_time_without_consuming_host_verification_reserve():
    allowance = turn_allowance(phase="generation", remaining=470.766, turn_timeout=300,
        verification_reserve=180, bounded_job=True)
    assert allowance == pytest.approx(290.766)
    assert turn_allowance(phase="review", remaining=470.766, turn_timeout=300,
        verification_reserve=0, bounded_job=True) == 60
    assert turn_allowance(phase="generation", remaining=190, turn_timeout=300,
        verification_reserve=180, bounded_job=True) == 10


@pytest.mark.parametrize("content", ["x" * 25000, "x" * MAX_AGENT_MESSAGE_CHARACTERS,
    "😀" * MAX_AGENT_MESSAGE_CHARACTERS], ids=["above-old-limit", "at-limit", "unicode-at-limit"])
def test_long_messages_pass_each_api_contract_and_survive_storage_reopening(tmp_path, content):
    assert AgentSessionMessageRequest(content=content).content == content
    assert AgentRuntimeStartRequest(project_id="p", instruction=content).instruction == content
    assert ChatMessage(content=content).content == content
    path = tmp_path / "sessions.sqlite3"
    store = AgentSessionStore(path)
    session = store.create(project_id="p")
    store.append_message(session.session_id, role="user", content=content)
    assert AgentSessionStore(path).get(session.session_id).messages[-1].content == content


def test_message_limit_rejects_oversize_without_replacing_a_saved_message(tmp_path):
    content = "x" * (MAX_AGENT_MESSAGE_CHARACTERS + 1)
    for model, values in [(AgentSessionMessageRequest, {"content": content}),
                          (AgentRuntimeStartRequest, {"project_id": "p", "instruction": content}),
                          (ChatMessage, {"content": content})]:
        with pytest.raises(ValidationError):
            model(**values)
    store = AgentSessionStore(tmp_path / "sessions.sqlite3")
    session = store.create(project_id="p")
    store.append_message(session.session_id, role="user", content="Saved instruction")
    with pytest.raises(ValueError, match="AGENT_MESSAGE_TOO_LARGE"):
        store.append_message(session.session_id, role="user", content=content)
    assert store.get(session.session_id).messages[-1].content == "Saved instruction"


def catalog_router(tmp_path, provider, *, cached=False):
    storage = SQLiteProviderSettingsStorage(tmp_path / "models.sqlite3", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    registry.configure_provider("openrouter", {"enabled": True})
    if cached:
        registry.save_models("openrouter", [ModelInfo("openrouter", f"fixture-{i}:free", f"Fixture {i}", free=True)
            for i in range(466)])
    return ModelRouterService(registry=registry, providers={"openrouter": provider})


class Discovery:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def list_models(self):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
            return [ModelInfo("openrouter", "new:free", "New free model", free=True)]
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_full_cached_catalog_uses_one_sqlite_snapshot(tmp_path, monkeypatch):
    router = catalog_router(tmp_path, Discovery(), cached=True)
    connections = []
    connect = router.registry.storage._connect
    def counted():
        connection = connect()
        connections.append(connection)
        return connection
    monkeypatch.setattr(router.registry.storage, "_connect", counted)
    cached, value = router.catalog_requests._view("openrouter")
    assert cached and value["count"] >= 466 and value["source"] == "cache"
    fixtures = [model for model in value["models"] if model["model_id"].startswith("fixture-")]
    assert len(fixtures) == 466 and all(model["free"] for model in fixtures)
    assert len(connections) == 1


def test_cached_response_finishes_while_provider_discovery_is_still_pending(tmp_path):
    provider = Discovery()
    router = catalog_router(tmp_path, provider, cached=True)
    async def run():
        try:
            value = await asyncio.wait_for(router.catalog_requests.models("openrouter"), timeout=2)
            assert value["source"] == "cache" and value["count"] >= 466
            await asyncio.wait_for(provider.started.wait(), timeout=2)
            assert not router.catalog_requests._tasks["openrouter"].done()
        finally:
            await router.aclose()
        assert provider.cancelled
    asyncio.run(run())


def test_discovery_is_shared_and_one_cancelled_request_does_not_cancel_the_refresh(tmp_path):
    provider = Discovery()
    router = catalog_router(tmp_path, provider)
    async def run():
        try:
            tasks = [asyncio.create_task(router.catalog_requests.models("openrouter", refresh=True)) for _ in range(8)]
            await asyncio.wait_for(provider.started.wait(), timeout=2)
            await asyncio.sleep(0.05)
            assert provider.calls == 1
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
            assert not provider.cancelled
            provider.release.set()
            values = await asyncio.gather(*tasks[1:])
            assert all(value["source"] == "provider" and value["models"][0]["model_id"] == "new:free" for value in values)
            await router.catalog_requests.models("openrouter")
            assert provider.calls == 1
        finally:
            await router.aclose()
    asyncio.run(run())


def test_slow_snapshot_reads_leave_the_event_loop_responsive(tmp_path, monkeypatch):
    router = catalog_router(tmp_path, Discovery(), cached=True)
    original = router.catalog_requests._view
    entered = threading.Event()
    def slow_view(provider):
        entered.set()
        time.sleep(0.25)
        return original(provider)
    monkeypatch.setattr(router.catalog_requests, "_view", slow_view)
    async def run():
        try:
            task = asyncio.create_task(router.catalog_requests.models("openrouter"))
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0.05)
            assert not task.done()  # The loop can service health requests during the storage read.
            assert (await task)["count"] >= 466
        finally:
            await router.aclose()
    asyncio.run(run())


def test_discovery_timeout_preserves_cached_models_and_reports_its_cause(tmp_path, monkeypatch):
    from backend.model_router import catalog_requests
    monkeypatch.setattr(catalog_requests, "CATALOG_REFRESH_TIMEOUT_SECONDS", 0.02)
    provider = Discovery()
    router = catalog_router(tmp_path, provider, cached=True)
    async def run():
        try:
            value = await router.catalog_requests.models("openrouter", refresh=True)
            assert value["source"] == "fallback" and value["count"] >= 466
            assert "TimeoutError" in value["error"]
            assert provider.cancelled
            assert (await router.catalog_requests.models("openrouter"))["count"] >= 466
            assert provider.calls == 1
        finally:
            await router.aclose()
    asyncio.run(run())
