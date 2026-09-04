"""Provider and model registry for the model router."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .models import ModelInfo, ModelProvider, ModelRoute, TaskType
from .storage import ProviderSettingsStorage, mask_secret


DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-20b:free"
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


PROVIDERS: dict[str, ProviderDefinition] = {
    "openrouter": ProviderDefinition(
        "openrouter",
        "OpenRouter API",
        "api_key",
        False,
        DEFAULT_OPENROUTER_MODEL,
        env_key="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
        env_model="OPENROUTER_MODEL",
        models=(DEFAULT_OPENROUTER_MODEL, "openai/gpt-oss-120b:free", "openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet"),
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
    "gemini": ProviderDefinition(
        "gemini",
        "Gemini API",
        "api_key",
        False,
        "gemini-1.5-flash",
        env_key="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        env_model="GEMINI_MODEL",
        models=("gemini-1.5-flash", "gemini-1.5-pro"),
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

FALLBACK_ORDER = ("openrouter", "openai", "groq")
LOCAL_FALLBACK_ORDER = ("lmstudio", "ollama")


class ProviderRegistry:
    def __init__(self, storage: ProviderSettingsStorage | None = None) -> None:
        self.storage = storage or ProviderSettingsStorage()

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(PROVIDERS)

    def definition(self, provider_id: str) -> ProviderDefinition:
        try:
            return PROVIDERS[provider_id]
        except KeyError as exc:
            raise ValueError(f"Unknown model provider: {provider_id}") from exc

    def provider(self, provider_id: str) -> ModelProvider:
        definition = self.definition(provider_id)
        settings = self.storage.provider_settings(provider_id)
        health = self.storage.provider_health(provider_id)
        cached_models = self.storage.provider_models(provider_id)
        api_key = self.api_key(provider_id)
        default_model = self.default_model(provider_id)
        base_url = self.base_url(provider_id)
        configured = definition.local or bool(api_key)
        enabled = bool(settings.get("enabled", configured)) and definition.available
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
        provider_type = "api_provider"
        return ModelProvider(
            provider_id=definition.provider_id,
            display_name=definition.display_name,
            auth_type=definition.auth_type,  # type: ignore[arg-type]
            provider_type=provider_type,
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
        )

    def list_providers(self) -> list[ModelProvider]:
        return [self.provider(provider_id) for provider_id in PROVIDERS]

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
                        free=item.get("free") if isinstance(item.get("free"), bool) else None,
                        local=bool(item.get("local", definition.local)),
                        supported_parameters=tuple(
                            value
                            for value in item.get("supported_parameters", [])
                            if isinstance(value, str)
                        ) if isinstance(item.get("supported_parameters"), list) else (),
                    )
                )
            if models:
                return models
        return [
            ModelInfo(
                provider_id=provider_id,
                model_id=model,
                display_name=model.split("/")[-1],
                free=":free" in model,
                local=definition.local,
            )
            for model in definition.models
        ]

    def configure_provider(
        self,
        provider_id: str,
        settings: dict[str, Any],
    ) -> ModelProvider:
        self.definition(provider_id)
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

    def model_info(self, provider_id: str, model_id: str) -> ModelInfo | None:
        normalized = _clean_model_id(model_id)
        return next(
            (model for model in self.list_models(provider_id) if model.model_id == normalized),
            None,
        )

    def api_key(self, provider_id: str) -> str | None:
        definition = self.definition(provider_id)
        if definition.local:
            return None
        stored = self.storage.api_key(provider_id)
        if stored:
            return stored
        if definition.env_key:
            value = os.getenv(definition.env_key, "").strip()
            if value:
                return value
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
            return _clean_model_id(raw)
        if definition.env_model:
            env_model = os.getenv(definition.env_model, "").strip()
            if env_model:
                return _clean_model_id(env_model)
        return definition.default_model

    def route_for_task(self, task_type: str) -> ModelRoute:
        routes = self.storage.routes()
        saved = routes.get(task_type)
        if saved:
            provider_id = str(saved.get("provider_id", "openrouter"))
            return ModelRoute(
                task_type=task_type,  # type: ignore[arg-type]
                provider_id=provider_id,
                model_id=str(saved.get("model_id") or self.default_model(provider_id)),
                fallback_enabled=bool(saved.get("fallback_enabled", True)),
                fallback_provider_id=(str(saved["fallback_provider_id"]) if saved.get("fallback_provider_id") else None),
                local_only=bool(saved.get("local_only", False)),
            )
        return ModelRoute(
            task_type=task_type,  # type: ignore[arg-type]
            provider_id="openrouter",
            model_id=self.default_model("openrouter"),
            fallback_enabled=True,
            fallback_provider_id="openai",
        )

    def list_routes(self) -> list[ModelRoute]:
        return [self.route_for_task(task_type) for task_type in TASK_TYPES]

    def save_route(self, route: ModelRoute) -> None:
        self.definition(route.provider_id)
        if route.fallback_provider_id:
            self.definition(route.fallback_provider_id)
            primary = self.definition(route.provider_id)
            fallback = self.definition(route.fallback_provider_id)
            if primary.local != fallback.local:
                raise ValueError("Cross-category fallback must be configured through an explicit confirmed run policy")
        self.storage.save_route(route.task_type, route.to_dict())

    def fallback_provider_ids(self, *, local_only: bool = False) -> tuple[str, ...]:
        return LOCAL_FALLBACK_ORDER if local_only else FALLBACK_ORDER


def _clean_model_id(value: str) -> str:
    return value.strip().strip("\"'")
