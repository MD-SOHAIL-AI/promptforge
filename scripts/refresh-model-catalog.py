"""Refresh OpenCode's Models.dev snapshot explicitly; startup stays offline."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "backend/model_router/data/models-dev.json"


def main() -> None:
    response = httpx.get("https://models.dev/api.json", headers={"User-Agent": "opencode/1.18.34"}, timeout=60, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not data:
        raise ValueError("Models.dev returned an empty or invalid catalog")
    dependencies = json.loads((ROOT / "package.json").read_text())["dependencies"]
    supported = set(dependencies) | {"@ai-sdk/amazon-bedrock/mantle", "@ai-sdk/google-vertex/anthropic", "@ai-sdk/github-copilot"}
    for provider_id, provider in data.items():
        if provider.get("id") != provider_id or not isinstance(provider.get("models"), dict):
            raise ValueError(f"Invalid provider record: {provider_id}")
        packages = {provider.get("npm")} | {model.get("provider", {}).get("npm", provider.get("npm")) for model in provider["models"].values()}
        if packages - supported:
            raise ValueError(f"Install and implement new provider packages before refreshing: {provider_id}: {packages - supported}")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    temporary = DESTINATION.with_suffix(".json.tmp")
    temporary.write_bytes(raw)
    os.replace(temporary, DESTINATION)
    metadata = {"source": "https://models.dev/api.json", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(raw).hexdigest(), "providers": len(data),
                "models": sum(len(provider["models"]) for provider in data.values())}
    DESTINATION.with_name("models-dev.metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {metadata['providers']} providers and {metadata['models']} models. Run npm run test:model-router and rebuild Electron.")


if __name__ == "__main__":
    main()
