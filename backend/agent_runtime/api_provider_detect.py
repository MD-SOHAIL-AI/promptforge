"""Non-network API planner readiness detection."""

from __future__ import annotations

import json
import os

from .product_provider_registry import ProductProviderRegistry


def detect(env=None) -> dict[str, object]:
    registry = ProductProviderRegistry(env=env if env is not None else os.environ)
    providers = [item for item in registry.safe_statuses() if item["kind"] == "api_planner" and item["provider_id"] in {"gemini", "groq", "openrouter", "openai", "nvidia_nim"}]
    return {"network_attempted": False, "providers": providers}


def main() -> int:
    print(json.dumps(detect(), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
