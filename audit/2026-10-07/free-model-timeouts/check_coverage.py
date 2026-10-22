"""Check timeout coverage without credentials, HTTP calls or production state."""
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.model_router.opencode_catalog import catalog, model_capabilities
from backend.model_router.transport_policy import FREE_TIER_PROVIDERS, provider_transport_policy

entries = []
failures = []
for provider_id, provider in catalog().items():
    for model_id, facts in provider.get("models", {}).items():
        pricing = facts.get("cost", {})
        if pricing.get("input") != 0 or pricing.get("output") != 0 or not model_capabilities(facts)["text_generation"]:
            continue
        policy = provider_transport_policy(provider_id, model_id, reasoning=facts.get("reasoning", False))
        entry = {"provider": provider_id, "model": model_id, "policy": asdict(policy)}
        entries.append(entry)
        if policy.idle_seconds < 120 or not 120 <= policy.first_event_seconds <= 180 or policy.total_seconds > 600:
            failures.append(entry)

result = {"catalog_zero_token_price_text_entries": len(entries),
    "provider_count": len({item["provider"] for item in entries}),
    "failures": failures,
    "entries_by_provider": dict(sorted(Counter(item["provider"] for item in entries).items())),
    "account_free_or_trial_provider_families": sorted(FREE_TIER_PROVIDERS),
    "classification_note": "Zero token prices are transport hints, not proof of free account access; subscriptions and credits can still apply.",
    "external_generation_requests": 0, "credentials_read": False, "production_state_used": False,
    "entries": entries}
Path(__file__).with_name("coverage.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
assert not failures, failures
print(json.dumps({key: value for key, value in result.items() if key != "entries"}, indent=2))
