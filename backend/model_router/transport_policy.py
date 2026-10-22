"""Bounded host allowances, not provider response-time guarantees.

Provider queueing, reasoning and local model loading affect the first event.
Explicit request policies win. Parent turn/job deadlines always win as well.
"""
from __future__ import annotations

from backend.contracts.transport import TransportPolicy
from .models import execution_value
from .opencode_catalog import model_data

# These providers share model IDs across free, trial and paid accounts. The
# transport must allow their free usage without claiming an account is free.
FREE_TIER_PROVIDERS = frozenset({"groq", "cerebras", "google", "gemini", "mistral", "cohere", "cloudflare-workers-ai"})


def provider_transport_policy(provider_id: str | None, model_id: str | None = None,
                              *, reasoning: bool = False, phase: str | None = None,
                              free: bool | None = None) -> TransportPolicy:
    provider = (provider_id or "").casefold()
    if free is None:
        pricing = model_data(provider, model_id or "").get("cost", {})
        # Catalog zero token prices also occur in subscription plans. This is
        # only a timing hint and never authorizes or classifies account billing.
        free = (model_id or "").endswith(":free") or (provider == "openrouter" and model_id == "openrouter/free") or (
            pricing.get("input") == 0 and pricing.get("output") == 0)
    # Reviews retain their existing short outer deadline and idle allowance.
    if phase == "review":
        return TransportPolicy()
    if provider in {"ollama", "lmstudio"}:
        return TransportPolicy(first_event_seconds=180, idle_seconds=120, total_seconds=600)
    if provider == "openrouter" and free:
        # Recorded first events reach 78 seconds. Keep the 120-second window
        # so a 180-second primary turn can still fund short-failure recovery.
        return TransportPolicy(idle_seconds=120)
    if provider in {"nvidia", "nvidia_nim"} and phase == "generation":
        return TransportPolicy(idle_seconds=120)
    if provider == "deepseek" or reasoning:
        return TransportPolicy(first_event_seconds=180, idle_seconds=120, total_seconds=600)
    if free or provider in FREE_TIER_PROVIDERS:
        return TransportPolicy(idle_seconds=120)
    return TransportPolicy()


def request_transport_policy(request, *, provider_id=None, model_id=None, reasoning=False, free=None):
    explicit = execution_value(request, "transport_policy", "forgex_transport_policy")
    if explicit is not None:
        return explicit
    return provider_transport_policy(provider_id or request.provider_id, model_id or request.model_id,
                                     reasoning=reasoning, phase=request.metadata.get("agent_phase"), free=free)
