"""Product-agent provider view backed by the canonical model registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.model_router.registry import ProviderRegistry as ModelProviderRegistry

from .api_planner_provider import API_PLANNER_CONFIGS, ApiPlannerProvider, ApiTransport
from .product_providers import FakeProductPlanner, ProductPlanner, VerifiedTemplatePlanner


@dataclass(frozen=True, slots=True)
class ProductProviderEntry:
    provider_id: str
    display_name: str
    kind: str
    state: str
    routeable: bool
    enabled_by_default: bool = False
    supports_toolplan: bool = True
    supports_streaming: bool = False
    key_present: bool = False
    model_configured: bool = False
    model_id: str | None = None
    health_status: str | None = None
    local: bool = False
    selected: bool = False

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "kind": self.kind,
            "provider_kind": self.kind,
            "state": self.state,
            "routeable": self.routeable,
            "enabled_by_default": self.enabled_by_default,
            "supports_toolplan": self.supports_toolplan,
            "supports_streaming": self.supports_streaming,
            "key_present": self.key_present,
            "model_configured": self.model_configured,
            "model_id": self.model_id,
            "health_status": self.health_status,
            "local": self.local,
            "selected": self.selected,
        }


class ProductProviderRegistry:
    """Expose only providers the model runtime already owns.

    No API keys, models, enable flags or fallbacks are re-created as environment
    variables. The model registry remains the sole configuration authority.
    """

    def __init__(
        self,
        *,
        model_provider_registry: ModelProviderRegistry,
        fake_enabled: bool = False,
        transport: ApiTransport | None = None,
        openrouter_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._model_provider_registry = model_provider_registry
        self._transport = transport
        self._fake_enabled = bool(fake_enabled)
        self._openrouter_headers = dict(openrouter_headers or {})
        self.last_planner: ProductPlanner | None = None

    def list(self) -> tuple[ProductProviderEntry, ...]:
        active_route = self._model_provider_registry.route_for_task("code_generation")
        entries: list[ProductProviderEntry] = [
            ProductProviderEntry(
                "fake_planner",
                "ForgeX Fake Planner",
                "fake",
                "enabled" if self._fake_enabled else "disabled",
                self._fake_enabled,
                enabled_by_default=False,
            ),
            ProductProviderEntry(
                "verified_template",
                "Verified template",
                "template_provider",
                "ready",
                True,
                enabled_by_default=True,
            ),
        ]
        for provider_id, config in API_PLANNER_CONFIGS.items():
            if provider_id not in self._model_provider_registry.provider_ids():
                continue
            provider = self._model_provider_registry.provider(provider_id)
            key_present = bool(self._model_provider_registry.api_key(provider_id))
            model_id = self._selected_model(provider_id)
            routeable = provider.enabled and provider.configured and key_present and bool(model_id)
            entries.append(
                ProductProviderEntry(
                    provider_id=provider_id,
                    display_name=config.display_name,
                    kind="api_planner",
                    state="ready" if routeable else provider.health_status,
                    routeable=routeable,
                    supports_streaming=True,
                    key_present=key_present,
                    model_configured=bool(model_id),
                    model_id=model_id or None,
                    health_status=provider.health_status,
                    local=provider.local,
                    selected=active_route.provider_id == provider_id,
                )
            )
        return tuple(sorted(entries, key=lambda item: item.provider_id))

    def resolve(self, provider_id: str) -> ProductPlanner:
        if provider_id == "verified_template":
            self.last_planner = VerifiedTemplatePlanner()
            return self.last_planner
        if provider_id == "fake_planner" and self._fake_enabled:
            self.last_planner = FakeProductPlanner()
            return self.last_planner

        config = API_PLANNER_CONFIGS.get(provider_id)
        if config is None or provider_id not in self._model_provider_registry.provider_ids():
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        provider = self._model_provider_registry.provider(provider_id)
        api_key = self._model_provider_registry.api_key(provider_id) or ""
        model_id = self._selected_model(provider_id)
        if not provider.enabled:
            raise ValueError("API_PROVIDER_DISABLED")
        if not provider.configured or not api_key:
            raise ValueError("API_PROVIDER_KEY_MISSING")
        if not model_id:
            raise ValueError("API_PROVIDER_MODEL_MISSING")

        headers = self._openrouter_headers if provider_id == "openrouter" else None
        model_info = self._model_provider_registry.model_info(provider_id, model_id)
        supported_parameters = model_info.supported_parameters if model_info is not None else ()
        route = self._model_provider_registry.route_for_task("code_generation")
        fallback_model_id = None
        fallback_supported_parameters: tuple[str, ...] = ()
        if provider_id == "openrouter" and route.fallback_enabled and model_id != "openrouter/free":
            fallback_model_id = "openrouter/free"
            fallback_info = self._model_provider_registry.model_info(provider_id, fallback_model_id)
            fallback_supported_parameters = (
                fallback_info.supported_parameters if fallback_info is not None else ("response_format",)
            )
        self.last_planner = ApiPlannerProvider(
            config,
            api_key=api_key,
            model_id=model_id,
            enabled=True,
            transport=self._transport,
            extra_headers=headers,
            endpoint=self._planner_endpoint(provider_id),
            supported_parameters=supported_parameters,
            fallback_model_id=fallback_model_id,
            fallback_supported_parameters=fallback_supported_parameters,
        )
        return self.last_planner

    def safe_statuses(self) -> tuple[dict[str, object], ...]:
        return tuple(item.to_safe_dict() for item in self.list())

    def default_provider_id(self) -> str:
        entries = self.list()
        selected = next((item for item in entries if item.selected and item.routeable), None)
        if selected is not None:
            return selected.provider_id
        template = next((item for item in entries if item.provider_id == "verified_template" and item.routeable), None)
        if template is not None:
            return template.provider_id
        fallback = next((item for item in entries if item.routeable), None)
        if fallback is None:
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        return fallback.provider_id

    def _planner_endpoint(self, provider_id: str) -> str:
        config = API_PLANNER_CONFIGS[provider_id]
        base_url = (self._model_provider_registry.base_url(provider_id) or "").rstrip("/")
        if not base_url:
            return config.endpoint
        if provider_id == "gemini":
            return f"{base_url}/openai/chat/completions"
        return f"{base_url}/chat/completions"

    def configured_fallback(self, provider_id: str) -> str | None:
        route = self._model_provider_registry.route_for_task("code_generation")
        fallback_id = route.fallback_provider_id
        if not route.fallback_enabled or route.provider_id != provider_id or not fallback_id:
            return None
        if fallback_id not in API_PLANNER_CONFIGS:
            return None
        primary = self._model_provider_registry.provider(provider_id)
        fallback = self._model_provider_registry.provider(fallback_id)
        if primary.local != fallback.local or not fallback.configured or not fallback.enabled:
            return None
        if not self._model_provider_registry.api_key(fallback_id):
            return None
        return fallback_id

    def fallback_for_instruction(self, provider_id: str, instruction: str) -> str | None:
        del instruction
        return self.configured_fallback(provider_id)

    def selected_model_id(self, provider_id: str) -> str | None:
        if provider_id == "verified_template":
            return "forgex-verified-templates-v1"
        if provider_id == "fake_planner":
            return "forgex-fake-planner-v1" if self._fake_enabled else None
        if provider_id not in API_PLANNER_CONFIGS:
            return None
        return self._selected_model(provider_id)

    def workflow_route_metadata(self, provider_id: str, *, fallback_provider_id: str | None = None) -> dict[str, object]:
        if provider_id == "verified_template":
            return {
                "provider_id": provider_id,
                "model_id": "forgex-verified-templates-v1",
                "fallback_enabled": False,
                "fallback_provider_id": None,
                "local_only": True,
            }
        if provider_id == "fake_planner":
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        if provider_id not in API_PLANNER_CONFIGS:
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        provider = self._model_provider_registry.provider(provider_id)
        api_key = self._model_provider_registry.api_key(provider_id)
        model_id = self._selected_model(provider_id)
        if not provider.enabled:
            raise ValueError("API_PROVIDER_DISABLED")
        if not provider.configured or not api_key:
            raise ValueError("API_PROVIDER_KEY_MISSING")
        if not model_id:
            raise ValueError("API_PROVIDER_MODEL_MISSING")
        fallback_id = fallback_provider_id or self.configured_fallback(provider_id)
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "fallback_enabled": bool(fallback_id),
            "fallback_provider_id": fallback_id,
            "local_only": provider.local,
        }

    def _selected_model(self, provider_id: str) -> str:
        route = self._model_provider_registry.route_for_task("code_generation")
        if route.provider_id == provider_id and route.model_id:
            return route.model_id
        return self._model_provider_registry.default_model(provider_id)
