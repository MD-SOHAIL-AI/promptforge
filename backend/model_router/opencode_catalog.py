"""OpenCode's Models.dev catalog, pinned for offline/reproducible startup."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path

ALIASES = {"gemini": "google", "nvidia_nim": "nvidia"}
QUARANTINED_SDKS = json.loads((Path(__file__).parent / "data" / "provider-quarantine.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((Path(__file__).parent / "data" / "models-dev.json").read_text(encoding="utf-8"))


def provider_data(provider_id: str) -> dict:
    return catalog().get(ALIASES.get(provider_id, provider_id), {})


def model_data(provider_id: str, model_id: str) -> dict:
    return provider_data(provider_id).get("models", {}).get(model_id, {})


def protocol(data: dict) -> str:
    npm = data.get("npm", "@ai-sdk/openai-compatible")
    return {"@ai-sdk/openai": "openai-responses", "@ai-sdk/azure": "azure", "@ai-sdk/anthropic": "anthropic-messages", "@ai-sdk/google": "gemini", "@ai-sdk/amazon-bedrock": "bedrock-converse"}.get(npm, "sdk" if npm not in {"@ai-sdk/openai-compatible", "@openrouter/ai-sdk-provider", "@ai-sdk/deepinfra", "@ai-sdk/groq", "@ai-sdk/cerebras", "@ai-sdk/togetherai", "@ai-sdk/xai"} else "openai-completions")


def expand_endpoint(value: str | None, options: dict) -> str | None:
    if not value:
        return None
    def replace(match):
        key = match.group(1)
        aliases = {"AWS_REGION": "region", "AZURE_RESOURCE_NAME": "resourceName", "CLOUDFLARE_ACCOUNT_ID": "accountId", "CLOUDFLARE_GATEWAY_ID": "gatewayId", "SNOWFLAKE_ACCOUNT": "account", "GOOGLE_VERTEX_PROJECT": "project", "GOOGLE_VERTEX_LOCATION": "location"}
        result = options.get(key) or options.get(aliases.get(key, key)) or os.getenv(key)
        if not result and key == "AWS_REGION": result = "us-east-1"
        if not isinstance(result, str) or not result.strip():
            raise ValueError(f"Configure {key} for this provider")
        return result.strip()
    return re.sub(r"\$\{([A-Z0-9_]+)\}", replace, value).rstrip("/")


def model_capabilities(data: dict) -> dict:
    outputs = data.get("modalities", {}).get("output", ["text"])
    return {"streaming": True, "text_generation": "text" in outputs and data.get("limit", {}).get("output") != 0,
            "json_object": bool(data.get("structured_output")), "json_schema": bool(data.get("structured_output")),
            "native_tools": bool(data.get("tool_call")), "reasoning": bool(data.get("reasoning")),
            "system_prompt": True, "vision": "image" in data.get("modalities", {}).get("input", []),
            "context_window": data.get("limit", {}).get("context")}
