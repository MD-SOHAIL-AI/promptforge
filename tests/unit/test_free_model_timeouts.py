"""Free catalog models and account free tiers use the same safe transport."""
import json

import pytest

from backend.contracts.transport import TransportPolicy
from backend.model_router.models import ModelCapabilities, ModelInfo, ModelRequest, RequestExecutionContext
from backend.model_router.opencode_catalog import catalog, model_data, model_capabilities
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router.transport_policy import provider_transport_policy
from tests.unit.test_opencode_engine import registry


def test_every_catalog_zero_price_text_model_gets_idle_grace():
    covered = []
    for provider_id, provider in catalog().items():
        for model_id, facts in provider.get("models", {}).items():
            pricing = facts.get("cost", {})
            if pricing.get("input") != 0 or pricing.get("output") != 0 or not model_capabilities(facts)["text_generation"]:
                continue
            policy = provider_transport_policy(provider_id, model_id, reasoning=facts.get("reasoning", False))
            assert policy.idle_seconds >= 120, (provider_id, model_id)
            assert 120 <= policy.first_event_seconds <= 180, (provider_id, model_id)
            assert policy.total_seconds <= 600, (provider_id, model_id)
            covered.append((provider_id, model_id))
    assert covered
    assert {"openrouter", "opencode", "nvidia", "zai", "kilo"} <= {provider for provider, _ in covered}


@pytest.mark.parametrize("provider_id,model_id", [
    ("groq", "openai/gpt-oss-120b"), ("cerebras", "gpt-oss-120b"),
    ("google", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash"),
    ("mistral", "mistral-small-latest"), ("cohere", "command-r-plus"),
    ("cloudflare-workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast"),
])
def test_account_free_tiers_do_not_require_zero_paid_tier_prices(tmp_path, provider_id, model_id):
    reg = registry(tmp_path)
    reg.configure_provider(provider_id, {"api_key": "fixture", "options": {"accountId": "test-account"}})
    context = RequestExecutionContext()
    payload, policy = OpenCodeProvider(reg, provider_id)._payload(ModelRequest(
        prompt="hello", model_id=model_id, execution_context=context))
    assert policy.idle_seconds == 120
    assert context.transport_policy is policy
    assert "free" not in payload and "billing_tier" not in payload


@pytest.mark.parametrize("model_id", ["openrouter/free", "stealth/space-bunny-alpha"])
def test_openrouter_free_models_without_suffix_are_covered(tmp_path, model_id):
    reg = registry(tmp_path)
    reg.configure_provider("openrouter", {"api_key": "fixture"})
    _, policy = OpenCodeProvider(reg, "openrouter")._payload(ModelRequest(prompt="hello", model_id=model_id))
    assert policy.first_event_seconds == 120
    assert policy.idle_seconds == 120


def test_live_free_model_metadata_and_overrides_win(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("openrouter", {"api_key": "fixture"})
    model_id = "new-free-preview-without-suffix"
    assert model_data("openrouter", model_id) == {}
    reg.save_models("openrouter", [ModelInfo("openrouter", model_id, model_id, free=True,
        capabilities=ModelCapabilities(streaming=True, reasoning=True))])
    context = RequestExecutionContext()
    _, policy = OpenCodeProvider(reg, "openrouter")._payload(ModelRequest(
        prompt="hello", model_id=model_id, execution_context=context))
    assert policy.first_event_seconds == policy.idle_seconds == 120
    assert context.transport_policy is policy
    explicit = TransportPolicy(first_event_seconds=.5, idle_seconds=.1, total_seconds=1)
    context = RequestExecutionContext(transport_policy=explicit)
    _, policy = OpenCodeProvider(reg, "openrouter")._payload(ModelRequest(
        prompt="hello", model_id=model_id, execution_context=context))
    assert policy is explicit


def test_cached_paid_metadata_is_not_replaced_by_pinned_free_prices(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("openrouter", {"api_key": "fixture"})
    model_id = "stealth/space-bunny-alpha"
    assert model_data("openrouter", model_id)["cost"]["input"] == 0
    reg.save_models("openrouter", [ModelInfo("openrouter", model_id, model_id, free=False,
        capabilities=ModelCapabilities(streaming=True, reasoning=False))])
    assert reg.model_is_free("openrouter", model_id) is False
    _, policy = OpenCodeProvider(reg, "openrouter")._payload(ModelRequest(prompt="hello", model_id=model_id))
    assert policy == TransportPolicy()


def test_local_models_and_review_bounds_are_preserved(tmp_path):
    reg = registry(tmp_path)
    for provider_id in ("lmstudio", "ollama"):
        assert reg.model_is_free(provider_id, "local-model") is True
        policy = provider_transport_policy(provider_id, "local-model", free=True)
        assert policy.first_event_seconds == 180 and policy.total_seconds == 600
    assert provider_transport_policy("openrouter", "openrouter/free", free=True, phase="review") == TransportPolicy()


def test_unknown_free_suffix_and_paid_selection_do_not_change_routes(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("openrouter", {"api_key": "fixture"})
    before = reg.storage.path.read_bytes()
    routes_before = json.dumps([route.to_dict() for route in reg.list_routes()], sort_keys=True)
    free = ModelRequest(prompt="hello", model_id="future-model:free")
    _, policy = OpenCodeProvider(reg, "openrouter")._payload(free)
    assert policy.idle_seconds == 120
    paid = ModelRequest(prompt="hello", model_id="openai/gpt-4o-mini")
    payload, policy = OpenCodeProvider(reg, "openrouter")._payload(paid)
    assert payload["model_id"] == paid.model_id and policy == TransportPolicy()
    assert reg.storage.path.read_bytes() == before
    assert json.dumps([route.to_dict() for route in reg.list_routes()], sort_keys=True) == routes_before
