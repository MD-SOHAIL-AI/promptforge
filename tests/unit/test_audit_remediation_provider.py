"""Current provider path protects outages and quarantined SDKs."""
import asyncio

import pytest

from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.opencode_catalog import catalog, QUARANTINED_SDKS
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router.router import ModelRouterService
from backend.model_router.usage import UsageTracker
from backend.services.llm_service import LLMConfigurationError, LLMProviderError
from tests.unit.test_opencode_engine import registry, upstream


def test_quarantined_provider_is_disabled_and_never_starts_a_worker(tmp_path, monkeypatch):
    provider_id = next(key for key, value in catalog().items() if value.get("npm") in QUARANTINED_SDKS)
    reg = registry(tmp_path)
    reg.configure_provider(provider_id, {"api_key": "fixture", "enabled": True})
    provider = reg.provider(provider_id)
    assert not provider.enabled
    assert provider.health_status == "disabled"
    assert "GHSA-86w9-cpqp-85rv" in provider.last_error
    async def forbidden_spawn(*args, **kwargs):
        pytest.fail("quarantined SDK launched a child")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    with pytest.raises(LLMConfigurationError) as caught:
        asyncio.run(OpenCodeProvider(reg, provider_id).generate(ModelRequest(prompt="hi",
            provider_id=provider_id, model_id=reg.default_model(provider_id))))
    assert caught.value.code == "PROVIDER_SDK_QUARANTINED"


def test_default_provider_records_six_failures_and_cools_next_request(tmp_path, monkeypatch):
    from backend.model_router import request_recovery
    real_sleep = asyncio.sleep
    async def fast_sleep(seconds): await real_sleep(0)
    monkeypatch.setattr(request_recovery.asyncio, "sleep", fast_sleep)
    sent = []
    def respond(handler, body):
        sent.append(body)
        handler.send_response(503)
        # Exercise the full physical allowance without a long-backoff handoff
        # to durable job recovery before the sixth attempt.
        handler.send_header("Retry-After", "0")
        handler.end_headers()
        handler.wfile.write(b'{"error":{"message":"overloaded"}}')
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "fixture", "base_url": url})
        owner = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path / "usage.jsonl"))
        assert isinstance(owner._providers["deepseek"], OpenCodeProvider)
        with pytest.raises(LLMProviderError):
            asyncio.run(owner.generate_model(ModelRequest(prompt="hi", provider_id="deepseek",
                model_id="deepseek-chat", execution_context=RequestExecutionContext())))
        assert len(sent) == 6
        identity = owner._circuit_identity("deepseek", "deepseek-chat")
        assert owner.circuit.health(identity)["state"] == "open"
        context = RequestExecutionContext(managed=False, attempts={"used": 0, "limit": 1})
        with pytest.raises(LLMProviderError) as caught:
            asyncio.run(owner.generate_model(ModelRequest(prompt="hi", provider_id="deepseek",
                model_id="deepseek-chat", execution_context=context)))
        assert caught.value.details["origin"] == "provider_circuit"
        assert len(sent) == 6
