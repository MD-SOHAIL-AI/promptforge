"""Provider and model registry for the model router."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Any

from .models import ModelCapabilities, ModelInfo, ModelProvider, ModelRoute, TaskType
from .storage import ProviderSettingsStorage, mask_secret
from .opencode_catalog import catalog as opencode_catalog, provider_data, model_data, model_capabilities as catalog_capabilities, protocol as catalog_protocol


DEFAULT_OPENROUTER_MODEL = "cohere/north-mini-code:free"
RETIRED_OPENROUTER_MODELS = frozenset({"minimax/minimax-m3:free"})
NVIDIA_NIM_MODELS = (
    "01-ai/yi-large",
    "adept/fuyu-8b",
    "ai21labs/jamba-1.5-large-instruct",
    "aisingapore/sea-lion-7b-instruct",
    "bigcode/starcoder2-15b",
    "databricks/dbrx-instruct",
    "deepseek-ai/deepseek-coder-6.7b-instruct",
    "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-pro-0813",
    "google/codegemma-1.1-7b",
    "google/codegemma-7b",
    "google/deplot",
    "google/diffusiongemma-26b-a4b-it",
    "google/gemma-2b",
    "google/gemma-3-12b-it",
    "google/gemma-3-4b-it",
    "google/gemma-4-31b-it",
    "google/recurrentgemma-2b",
    "ibm/granite-3.0-3b-a800m-instruct",
    "ibm/granite-3.0-8b-instruct",
    "ibm/granite-34b-code-instruct",
    "ibm/granite-8b-code-instruct",
    "meta/codellama-70b",
    "meta/llama2-70b",
    "meta/llama-3.2-11b-vision-instruct",
    "meta/llama-3.2-90b-vision-instruct",
    "meta/llama-guard-4-12b",
    "meta/muse-glimmer-30b",
    "microsoft/kosmos-2",
    "microsoft/phi-3.5-moe-instruct",
    "microsoft/phi-3-vision-128k-instruct",
    "mistralai/codestral-22b-instruct-v0.1",
    "mistralai/mistral-7b-instruct-v0.3",
    "mistralai/mistral-large",
    "mistralai/mistral-large-2-instruct",
    "mistralai/mistral-nemotron",
    "mistralai/mixtral-8x22b-v0.1",
    "moonshotai/kimi-k2.6",
    "moonshotai/kimi-k3",
    "nvidia/ai-synthetic-video-detector",
    "nvidia/cosmos-reason2-8b",
    "nvidia/embed-qa-4",
    "nvidia/ising-calibration-1.5-31b",
    "nvidia/llama-3.1-nemoguard-8b-content-safety",
    "nvidia/llama-3.1-nemoguard-8b-topic-control",
    "nvidia/llama-3.1-nemotron-51b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/llama-3.1-nemotron-safety-guard-8b-v3",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1",
    "nvidia/llama-3.2-nv-embedqa-1b-v1",
    "nvidia/llama3-chatqa-1.5-70b",
    "nvidia/llama-nemotron-embed-vl-1b-v2",
    "nvidia/mistral-nemo-minitron-8b-8k-instruct",
    "nvidia/nemotron-3.5-content-safety",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-embed-1b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/nemotron-4-340b-reward",
    "nvidia/nemotron-nano-3-30b-a3b",
    "nvidia/nemotron-parse",
    "nvidia/neva-22b",
    "nvidia/nvclip",
    "nvidia/nv-embedqa-mistral-7b-v2",
    "nvidia/riva-translate-4b-instruct",
    "nvidia/riva-translate-4b-instruct-v1.1",
    "nvidia/riva-translate-4b-instruct-v2",
    "nvidia/vila",
    "nv-mistralai/mistral-nemo-12b-instruct",
    "openai/gpt-oss-20b",
    "poolside/laguna-xs-2.1",
    "snowflake/arctic-embed-l",
    "writer/palmyra-creative-122b",
    "writer/palmyra-fin-70b-32k",
    "writer/palmyra-med-70b",
    "writer/palmyra-med-70b-32k",
    "zyphra/zamba2-7b-instruct",
)
TASK_TYPES: tuple[TaskType, ...] = (
    "code_generation",
    "planning",
    "debugging",
    "documentation",
    "serial_analysis",
    "hardware_analysis",
    "testing",
    "review",
    "general_chat",
)


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider_id: str
    display_name: str
    auth_type: str
    local: bool
    default_model: str
    env_key: str | None = None
    base_url: str | None = None
    env_model: str | None = None
    models: tuple[str, ...] = ()
    available: bool = True
    provider_type: str = "api_provider"
    protocol: str = "openai-completions"


PROVIDERS: dict[str, ProviderDefinition] = {
    "deepseek": ProviderDefinition(
        "deepseek",
        "DeepSeek API",
        "api_key",
        False,
        "deepseek-chat",
        env_key="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        env_model="DEEPSEEK_MODEL",
        models=("deepseek-chat", "deepseek-reasoner"),
    ),
    "anthropic": ProviderDefinition(
        "anthropic",
        "Anthropic API",
        "api_key",
        False,
        "claude-sonnet-4-5",
        env_key="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com/v1",
        env_model="ANTHROPIC_MODEL",
        models=("claude-sonnet-4-5", "claude-haiku-4-5"),
        protocol="anthropic-messages",
    ),
    "openrouter": ProviderDefinition(
        "openrouter",
        "OpenRouter API",
        "api_key",
        False,
        DEFAULT_OPENROUTER_MODEL,
        env_key="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        env_model="OPENROUTER_MODEL",
        models=(DEFAULT_OPENROUTER_MODEL, "cohere/north-mini-code:free", "opencode/union-alpha", "openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet"),
    ),
    "openai": ProviderDefinition(
        "openai",
        "OpenAI API",
        "api_key",
        False,
        "gpt-4o-mini",
        env_key="OPENAI_API_KEY",
        base_url="https://api.openai.com/v1",
        env_model="OPENAI_MODEL",
        models=("gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"),
        protocol="openai-responses",
    ),
    "groq": ProviderDefinition(
        "groq",
        "Groq API",
        "api_key",
        False,
        "llama-3.3-70b-versatile",
        env_key="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        env_model="GROQ_MODEL",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    ),
    "cerebras": ProviderDefinition(
        "cerebras",
        "Cerebras",
        "api_key",
        False,
        "gpt-oss-120b",
        env_key="CEREBRAS_API_KEY",
        base_url="https://api.cerebras.ai/v1",
        env_model="CEREBRAS_MODEL",
        models=("gpt-oss-120b",),
    ),
    "nvidia_nim": ProviderDefinition(
        "nvidia_nim",
        "NVIDIA NIM",
        "api_key",
        False,
        "nvidia/nemotron-3-super-120b-a12b",
        env_key="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
        env_model="NVIDIA_MODEL",
        models=NVIDIA_NIM_MODELS,
    ),
    "gemini": ProviderDefinition(
        "gemini",
        "Gemini API",
        "api_key",
        False,
        "gemini-2.5-flash",
        env_key="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        env_model="GEMINI_MODEL",
        models=("gemini-2.5-flash", "gemini-2.5-pro"),
        protocol="google-generate-content",
    ),
    "lmstudio": ProviderDefinition(
        "lmstudio",
        "LM Studio",
        "none",
        True,
        "local-model",
        base_url="http://localhost:1234/v1",
        env_model="LMSTUDIO_MODEL",
        models=("local-model",),
    ),
    "ollama": ProviderDefinition(
        "ollama",
        "Ollama",
        "none",
        True,
        "llama3.1",
        base_url="http://localhost:11434",
        env_model="OLLAMA_MODEL",
        models=("llama3.1", "codellama", "mistral"),
    ),

}

FALLBACK_ORDER = ("deepseek", "openrouter", "openai", "anthropic", "groq", "cerebras", "nvidia_nim")
LOCAL_FALLBACK_ORDER = ("lmstudio", "ollama")
BUILTIN_PROVIDER_ORDER = (
    "openrouter",
    "openai",
    "groq",
    "cerebras",
    "nvidia_nim",
    "gemini",
    "lmstudio",
    "ollama",
    "deepseek",
    "anthropic",
)


class ProviderRegistry:
    def __init__(self, storage: ProviderSettingsStorage | None = None) -> None:
        self.storage = storage or ProviderSettingsStorage()

    def provider_ids(self) -> tuple[str, ...]:
        return BUILTIN_PROVIDER_ORDER + tuple(sorted(set(PROVIDERS) - set(BUILTIN_PROVIDER_ORDER))) + tuple(sorted(self.storage.custom_provider_definitions()))

    def builtin_provider_ids(self) -> tuple[str, ...]:
        return tuple(PROVIDERS)

    def provider_options(self, provider_id: str) -> dict[str, Any]:
        self.definition(provider_id)
        value = self.storage.credential_store.get(provider_id + "-options")
        options = json.loads(value) if value else {}
        # Match OpenCode's env discovery without treating project/region names
        # as credentials. Explicit provider options take precedence.
        env_options = {"AWS_REGION": "region", "AWS_PROFILE": "profile", "GOOGLE_VERTEX_PROJECT": "project", "GOOGLE_VERTEX_LOCATION": "location", "CLOUDFLARE_ACCOUNT_ID": "accountId", "CLOUDFLARE_GATEWAY_ID": "gatewayId", "WATSONX_AI_PROJECT_ID": "projectId"}
        if provider_id in {"azure", "azure-cognitive-services"}:
            env_options["AZURE_COGNITIVE_SERVICES_RESOURCE_NAME" if provider_id == "azure-cognitive-services" else "AZURE_RESOURCE_NAME"] = "resourceName"
        for name, key in env_options.items():
            if os.getenv(name) and key not in options:
                options[key] = os.environ[name]
        return options

    def definition(self, provider_id: str) -> ProviderDefinition:
        if provider_id in PROVIDERS:
            return PROVIDERS[provider_id]
        value = self.storage.custom_provider_definitions().get(provider_id)
        if value is None:
            raise ValueError(f"Unknown model provider: {provider_id}")
        return ProviderDefinition(
            provider_id=provider_id,
            display_name=str(value["display_name"]),
            auth_type=str(value.get("auth_type") or "api_key"),
            local=bool(value.get("local", False)),
            default_model=str(value["default_model"]),
            base_url=str(value["base_url"]),
            models=tuple(str(item) for item in value.get("models", [])),
            protocol=str(value["protocol"]),
        )

    def provider(self, provider_id: str) -> ModelProvider:
        from .opencode_catalog import QUARANTINED_SDKS
        definition = self.definition(provider_id)
        settings = self.storage.provider_settings(provider_id)
        health = self.storage.provider_health(provider_id)
        cached_models = self.storage.provider_models(provider_id)
        api_key = self.api_key(provider_id)
        default_model = self.default_model(provider_id)
        base_url = self.base_url(provider_id)
        options = self.provider_options(provider_id)
        cloud_auth = provider_id == "amazon-bedrock" and bool(options.get("credentials") or options.get("profile") or os.getenv("AWS_PROFILE") or (os.getenv("AWS_ACCESS_KEY_ID") and os.getenv("AWS_SECRET_ACCESS_KEY")) or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE") or os.getenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI") or os.getenv("AWS_CONTAINER_CREDENTIALS_FULL_URI"))
        cloud_auth = cloud_auth or provider_id.startswith("google-vertex") and bool(options.get("project") or options.get("googleAuthOptions") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        configured = bool(api_key) or bool(cloud_auth) if definition.auth_type == "api_key" else definition.local or definition.auth_type == "none"
        enabled = bool(settings.get("enabled", configured)) and definition.available
        quarantine_reason = QUARANTINED_SDKS.get(provider_data(provider_id).get("npm"))
        if quarantine_reason:
            enabled = False
        if not configured and not definition.local:
            enabled = False
        health_status = str(health.get("status") or "unknown")
        last_checked_at = health.get("checked_at") if isinstance(health.get("checked_at"), str) else None
        last_error = health.get("message") if isinstance(health.get("message"), str) else None
        if not configured:
            health_status = "not_configured"
            last_checked_at = None
            last_error = f"Add an API key to use {definition.display_name}."
        elif not definition.available:
            health_status = "disabled"
            last_error = "Adapter prepared for a later release."
        elif not enabled:
            health_status = "disabled"
        if quarantine_reason:
            health_status = "disabled"
            last_error = quarantine_reason
        return ModelProvider(
            provider_id=definition.provider_id,
            display_name=definition.display_name,
            auth_type=definition.auth_type,  # type: ignore[arg-type]
            provider_type=definition.provider_type,  # type: ignore[arg-type]
            local=definition.local,
            default_model=default_model,
            base_url=base_url,
            enabled=enabled,
            configured=configured,
            api_key_masked=mask_secret(api_key),
            masked_api_key=mask_secret(api_key),
            health_status=health_status,
            last_checked_at=last_checked_at,
            last_error=last_error,
            models_cached=len(cached_models),
            custom=provider_id not in PROVIDERS,
            protocol=definition.protocol,
            configuration_fields=tuple(provider_data(provider_id).get("env", [])),
            options_configured=bool(self.storage.credential_store.get(provider_id + "-options")),
        )

    def list_providers(self) -> list[ModelProvider]:
        with self.storage.read_snapshot():
            return [self.provider(provider_id) for provider_id in self.provider_ids()]

    def register_custom_provider(
        self,
        *,
        provider_id: str,
        display_name: str,
        protocol: str,
        base_url: str,
        default_model: str,
        models: tuple[str, ...] = (),
        local: bool = False,
        api_key: str | None = None,
    ) -> ModelProvider:
        provider_id = provider_id.strip().casefold()
        if not provider_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in provider_id):
            raise ValueError("Custom provider ID is invalid")
        if provider_id in PROVIDERS or provider_id in self.storage.custom_provider_definitions():
            raise ValueError("Custom provider ID already exists")
        if protocol not in {"openai-completions", "openai-responses", "anthropic-messages"}:
            raise ValueError("Custom provider protocol is invalid")
        if not display_name.strip() or not default_model.strip() or not base_url.startswith(("http://", "https://")):
            raise ValueError("Custom provider definition is invalid")
        unique_models = tuple(dict.fromkeys((default_model.strip(), *(item.strip() for item in models if item.strip()))))
        self.storage.save_custom_provider_definition(provider_id, {
            "display_name": display_name.strip(),
            "protocol": protocol,
            "base_url": base_url.rstrip("/"),
            "default_model": default_model.strip(),
            "models": list(unique_models),
            "local": bool(local),
            "auth_type": "none" if local and not api_key else "api_key",
        })
        self.storage.save_provider_settings(provider_id, {
            "base_url": base_url.rstrip("/"),
            "default_model": default_model.strip(),
            "enabled": True,
            **({"api_key": api_key} if api_key else {}),
        })
        return self.provider(provider_id)

    def delete_custom_provider(self, provider_id: str) -> None:
        if provider_id in PROVIDERS:
            raise ValueError("Built-in providers cannot be deleted")
        self.definition(provider_id)
        referenced_by = [
            route.task_type
            for route in self.list_routes()
            if route.provider_id == provider_id or route.fallback_provider_id == provider_id
        ]
        if referenced_by:
            raise ValueError(
                "Custom provider is referenced by routes: "
                + ", ".join(referenced_by)
            )
        self.storage.credential_store.delete(provider_id)
        self.storage.credential_store.delete(provider_id + "-options")
        self.storage.delete_provider_state(provider_id)

    def list_models(self, provider_id: str) -> list[ModelInfo]:
        definition = self.definition(provider_id)
        cached = self.storage.provider_models(provider_id)
        if cached:
            models: list[ModelInfo] = []
            for item in cached:
                model_id = item.get("model_id")
                if not isinstance(model_id, str) or not model_id.strip():
                    continue
                models.append(
                    ModelInfo(
                        provider_id=provider_id,
                        model_id=model_id,
                        display_name=str(item.get("display_name") or model_id),
                        context_window=item.get("context_window") if isinstance(item.get("context_window"), int) else None,
                        free=(
                            True
                            if provider_id == "nvidia_nim"
                            else item.get("free") if isinstance(item.get("free"), bool) else None
                        ),
                        local=bool(item.get("local", definition.local)),
                        capabilities=self.model_capabilities(provider_id, model_id),
                    )
                )
            if models:
                if provider_id == "nvidia_nim":
                    cached_ids = {model.model_id for model in models}
                    models.extend(
                        ModelInfo(
                            provider_id=provider_id,
                            model_id=model_id,
                            display_name=model_id.split("/")[-1],
                            free=True,
                            local=False,
                            capabilities=self.model_capabilities(provider_id, model_id),
                        )
                        for model_id in definition.models
                        if model_id not in cached_ids
                    )
                known = {model.model_id for model in models}
                models.extend(self._catalog_models(provider_id, known))
                return models
        defaults = [
            ModelInfo(
                provider_id=provider_id,
                model_id=model,
                display_name=model.split("/")[-1],
                free=True if provider_id == "nvidia_nim" else ":free" in model,
                local=definition.local,
                capabilities=self.model_capabilities(provider_id, model),
            )
            for model in definition.models
        ]
        return defaults + self._catalog_models(provider_id, {model.model_id for model in defaults})

    def _catalog_models(self, provider_id: str, excluded: set[str]) -> list[ModelInfo]:
        return [ModelInfo(provider_id, model_id, str(data.get("name") or model_id),
            context_window=data.get("limit", {}).get("context"),
            free=data.get("cost", {}).get("input") == 0 and data.get("cost", {}).get("output") == 0,
            capabilities=ModelCapabilities(**catalog_capabilities(data)))
            for model_id, data in provider_data(provider_id).get("models", {}).items() if model_id not in excluded]

    def model_capabilities(
        self, provider_id: str, model_id: str
    ) -> ModelCapabilities:
        """Resolve capabilities for an exact provider/model pair.

        OpenRouter metadata is authoritative when cached. Unknown routed
        models remain conservative instead of inheriting provider-wide claims.
        """

        model = self.resolve_model_id(provider_id, model_id).casefold()
        for item in self.storage.provider_models(provider_id):
            if str(item.get("model_id", "")).casefold() != model:
                continue
            explicit = item.get("capabilities")
            if isinstance(explicit, dict):
                context_window = explicit.get("context_window")
                return ModelCapabilities(**{
                    key: (
                        context_window
                        if key == "context_window"
                        and isinstance(context_window, int)
                        and not isinstance(context_window, bool)
                        and context_window > 0
                        else None
                        if key == "context_window"
                        else bool(explicit.get(key, False))
                    )
                    for key in ModelCapabilities.__dataclass_fields__
                })
            supported = item.get("supported_parameters")
            if isinstance(supported, list):
                parameters = {str(value).casefold() for value in supported}
                return ModelCapabilities(
                    streaming=True,
                    json_object="response_format" in parameters,
                    json_schema="structured_outputs" in parameters,
                    native_tools="tools" in parameters,
                    reasoning=bool({"reasoning", "include_reasoning"} & parameters),
                    system_prompt=True,
                )
        facts = model_data(provider_id, model_id)
        if facts:
            return ModelCapabilities(**catalog_capabilities(facts))
        if provider_id == "openai":
            modern = any(token in model for token in ("gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4"))
            return ModelCapabilities(True, modern, modern, modern, any(token in model for token in ("gpt-5", "o1", "o3", "o4")), True)
        if provider_id == "gemini":
            modern = model.startswith(("gemini-2", "gemini-3"))
            return ModelCapabilities(True, modern, modern, modern, "thinking" in model or modern, True)
        if provider_id == "anthropic":
            return ModelCapabilities(True, False, False, True, "claude" in model, True)
        if provider_id == "deepseek":
            return ModelCapabilities(True, True, False, True, "reasoner" in model, True)
        if provider_id == "nvidia_nim" and model in {
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
        }:
            return ModelCapabilities(
                streaming=True,
                json_object=False,
                json_schema=False,
                native_tools=True,
                reasoning=True,
                system_prompt=True,
                context_window=1_000_000,
            )
        definition = self.definition(provider_id)
        if definition.protocol == "openai-responses":
            return ModelCapabilities(True, True, True, True, True, True)
        if definition.protocol == "anthropic-messages":
            return ModelCapabilities(True, False, False, True, True, True)
        # OpenRouter, Groq and local OpenAI-compatible servers vary by model.
        # Their discovered per-model metadata may opt into richer features.
        return ModelCapabilities(streaming=True, system_prompt=True)

    def configure_provider(
        self,
        provider_id: str,
        settings: dict[str, Any],
    ) -> ModelProvider:
        self.definition(provider_id)
        if "options" in settings:
            options = settings["options"]
            if options is None:
                self.storage.credential_store.delete(provider_id + "-options")
            elif isinstance(options, dict):
                encoded = json.dumps(options, allow_nan=False)
                if len(encoded.encode("utf-16-le")) > 2500:
                    raise ValueError("Provider options exceed secure credential storage limit")
                self.storage.credential_store.set(provider_id + "-options", encoded)
            else:
                raise ValueError("Provider options must be an object")
        self.storage.save_provider_settings(provider_id, settings)
        return self.provider(provider_id)

    def save_health(self, provider_id: str, health: dict[str, Any]) -> None:
        self.definition(provider_id)
        self.storage.save_provider_health(provider_id, health)

    def save_models(self, provider_id: str, models: list[ModelInfo]) -> None:
        self.definition(provider_id)
        self.storage.save_provider_models(
            provider_id,
            [model.to_dict() for model in models],
        )

    def api_key(self, provider_id: str) -> str | None:
        definition = self.definition(provider_id)
        if definition.auth_type != "api_key":
            return None
        stored = self.storage.api_key(provider_id)
        if stored:
            return stored
        if definition.env_key:
            value = os.getenv(definition.env_key, "").strip()
            if value:
                return value
        options = self.provider_options(provider_id)
        for key in ("apiKey", "token"):
            if isinstance(options.get(key), str) and options[key].strip():
                return options[key].strip()
        for name in provider_data(provider_id).get("env", []):
            if any(token in name for token in ("KEY", "TOKEN", "PAT")) and name not in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}:
                value = os.getenv(name, "").strip()
                if value:
                    return value
        if provider_id == "opencode":
            return "public"  # OpenCode Zen's upstream public/free-model auth.
        return None

    def base_url(self, provider_id: str) -> str | None:
        definition = self.definition(provider_id)
        settings = self.storage.provider_settings(provider_id)
        raw = settings.get("base_url")
        if isinstance(raw, str) and raw.strip():
            return raw.strip().rstrip("/")
        return definition.base_url

    def default_model(self, provider_id: str) -> str:
        definition = self.definition(provider_id)
        settings = self.storage.provider_settings(provider_id)
        raw = settings.get("default_model")
        if isinstance(raw, str) and raw.strip():
            return self.resolve_model_id(provider_id, raw)
        if definition.env_model:
            env_model = os.getenv(definition.env_model, "").strip()
            if env_model:
                return _clean_model_id(env_model)
        return definition.default_model

    def resolve_model_id(self, provider_id: str, model_id: str) -> str:
        """Replace ForgeX defaults that a provider has retired.

        User-entered model IDs remain authoritative. This migration is limited
        to model IDs that ForgeX itself previously shipped as defaults.
        """

        cleaned = _clean_model_id(model_id)
        if provider_id == "openrouter" and cleaned in RETIRED_OPENROUTER_MODELS:
            return DEFAULT_OPENROUTER_MODEL
        return cleaned

    def route_for_task(self, task_type: str) -> ModelRoute:
        routes = self.storage.routes()
        saved = routes.get(task_type)
        if saved:
            provider_id = str(saved.get("provider_id", "openrouter"))
            return ModelRoute(
                task_type=task_type,  # type: ignore[arg-type]
                provider_id=provider_id,
                model_id=self.resolve_model_id(
                    provider_id,
                    str(saved.get("model_id") or self.default_model(provider_id)),
                ),
                fallback_enabled=False,
                fallback_provider_id=None,
                local_only=bool(saved.get("local_only", False)),
            )
        return ModelRoute(
            task_type=task_type,  # type: ignore[arg-type]
            provider_id="openrouter",
            model_id=self.default_model("openrouter"),
            fallback_enabled=False,
            fallback_provider_id=None,
        )

    def list_routes(self) -> list[ModelRoute]:
        return [self.route_for_task(task_type) for task_type in TASK_TYPES]

    def save_route(self, route: ModelRoute) -> None:
        self.definition(route.provider_id)
        primary = self.definition(route.provider_id)
        if route.local_only and not primary.local:
            raise ValueError("local_only routes require a local provider")
        if route.fallback_provider_id == route.provider_id:
            raise ValueError("fallback provider cannot equal the primary provider")
        # Fallback fields are accepted for persisted-schema compatibility only.
        normalized = ModelRoute(
            route.task_type, route.provider_id, self.resolve_model_id(route.provider_id, route.model_id),
            fallback_enabled=False, fallback_provider_id=None, local_only=route.local_only,
        )
        self.storage.save_route(route.task_type, normalized.to_dict())

    def fallback_provider_ids(self, *, local_only: bool = False) -> tuple[str, ...]:
        return LOCAL_FALLBACK_ORDER if local_only else FALLBACK_ORDER


def _clean_model_id(value: str) -> str:
    return value.strip().strip("\"'")


# OpenCode uses this same provider/model catalog, with protocol-specific loaders.
# Existing ForgeX identifiers/defaults remain valid during migration.
for _id, _data in opencode_catalog().items():
    if _id in PROVIDERS:
        continue
    _models = tuple(_data.get("models", {}))
    _generative = [key for key in _models if catalog_capabilities(_data["models"][key])["text_generation"]]
    PROVIDERS[_id] = ProviderDefinition(_id, _data.get("name", _id), "api_key", False,
        _generative[0] if _generative else (_models[0] if _models else "default"),
        env_key=next((name for name in _data.get("env", []) if name not in {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"} and any(word in name for word in ("KEY", "TOKEN", "PAT"))), None),
        base_url=_data.get("api"), models=(), protocol=catalog_protocol(_data))
