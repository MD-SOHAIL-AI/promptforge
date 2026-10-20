"""Product-agent provider view backed by the canonical model registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from backend.model_router.registry import ProviderRegistry as ModelProviderRegistry
from backend.model_router.router import ModelRouterService

from .api_planner_provider import API_PLANNER_CONFIGS, ApiTransport
from .product_providers import FakeProductPlanner, ProductPlanner, VerifiedTemplatePlanner
from .router_planner import RouterProductPlanner


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
        model_router: ModelRouterService | None = None,
        template_enabled: bool = False,
    ) -> None:
        self._model_provider_registry = model_provider_registry
        self._transport = transport
        self._fake_enabled = bool(fake_enabled)
        self._openrouter_headers = dict(openrouter_headers or {})
        self._model_router = model_router or ModelRouterService(
            registry=model_provider_registry
        )
        self._template_enabled = bool(template_enabled or fake_enabled)
        self.last_planner: ProductPlanner | None = None

    def list(self) -> tuple[ProductProviderEntry, ...]:
        with self._model_provider_registry.storage.read_snapshot():
            return self._list()

    def _list(self) -> tuple[ProductProviderEntry, ...]:
        active_route = self._model_provider_registry.route_for_task("code_generation")
        entries: list[ProductProviderEntry] = []
        # Test doubles are absent from the production provider catalogue. They
        # are exposed only when an explicit development/test flag enables them.
        if self._fake_enabled:
            entries.append(ProductProviderEntry(
                "fake_planner",
                "ForgeX Fake Planner",
                "fake",
                "enabled",
                True,
                enabled_by_default=False,
            ))
        if self._template_enabled:
            entries.append(
                ProductProviderEntry(
                    "verified_template",
                    "Verified template (development only)",
                    "template_provider",
                    "ready",
                    True,
                    enabled_by_default=False,
                )
            )
        for provider_id in self._model_provider_registry.provider_ids():
            provider = self._model_provider_registry.provider(provider_id)
            key_present = bool(self._model_provider_registry.api_key(provider_id))
            model_id = self._selected_model(provider_id)
            capabilities = self._model_provider_registry.model_capabilities(
                provider_id, model_id
            ) if model_id else None
            routeable = (
                provider.enabled
                and provider.configured
                and bool(model_id)
            )
            entries.append(
                ProductProviderEntry(
                    provider_id=provider_id,
                    display_name=provider.display_name,
                    kind="api_planner",
                    state="ready" if routeable else provider.health_status,
                    routeable=routeable,
                    key_present=key_present,
                    model_configured=bool(model_id),
                    model_id=model_id or None,
                    health_status=provider.health_status,
                    local=provider.local,
                    selected=active_route.provider_id == provider_id,
                    supports_streaming=bool(capabilities and capabilities.streaming),
                )
            )
        return tuple(sorted(entries, key=lambda item: item.provider_id))

    def resolve(self, provider_id: str, *, model_id: str | None = None, session_id: str | None = None) -> ProductPlanner:
        if provider_id == "verified_template":
            if not self._template_enabled:
                raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
            self.last_planner = VerifiedTemplatePlanner()
            return self.last_planner
        if provider_id == "fake_planner" and self._fake_enabled:
            self.last_planner = FakeProductPlanner()
            return self.last_planner
        if provider_id not in self._model_provider_registry.provider_ids():
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        provider = self._model_provider_registry.provider(provider_id)
        selected_model = model_id or self._selected_model(provider_id)
        if not provider.enabled:
            raise ValueError("API_PROVIDER_DISABLED")
        if not provider.configured:
            raise ValueError("API_PROVIDER_KEY_MISSING")
        if not selected_model:
            raise ValueError("API_PROVIDER_MODEL_MISSING")
        # All API-backed Agent planning flows through the canonical ModelRuntime.
        self.last_planner = RouterProductPlanner(
            self._model_router,
            provider_id=provider_id,
            model_id=selected_model,
            session_id=session_id,
            fallback_provider_id=None,
            fallback_model_id=None,
            max_tokens=8192,
            # The turn owner must outlive the adapter's per-request deadline so
            # same-model retry and error normalization can finish cleanly.
            turn_timeout_s=300.0,
        )
        return self.last_planner

    def safe_statuses(self) -> tuple[dict[str, object], ...]:
        return tuple(item.to_safe_dict() for item in self.list())

    def default_provider_id(self) -> str:
        entries = self.list()
        selected = next((item for item in entries if item.selected and item.routeable), None)
        if selected is not None:
            return selected.provider_id
        # An unavailable selected provider must not send a user's repository
        # to an unrelated configured provider without an explicit choice.
        raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")

    def session_model(self, session_id: str | None) -> tuple[str, str] | None:
        selected, _ = self._model_router.selections.resolve(session_id=session_id)
        if selected is None:
            return None
        # Validate the persisted choice each turn; never substitute another model.
        from backend.model_router.models import ModelRequest
        resolved = self._model_router.resolver.resolve(ModelRequest(
            prompt="", task_type="code_generation", provider_id=selected.provider_id,
            model_id=selected.model_id,
        ))
        return resolved.provider_id, resolved.model_id

    def _planner_endpoint(self, provider_id: str) -> str:
        config = API_PLANNER_CONFIGS[provider_id]
        base_url = (self._model_provider_registry.base_url(provider_id) or "").rstrip("/")
        if not base_url:
            return config.endpoint
        if provider_id == "gemini":
            return f"{base_url}/openai/chat/completions"
        return f"{base_url}/chat/completions"

    def selected_model_id(self, provider_id: str) -> str | None:
        if provider_id == "verified_template":
            return "forgex-verified-templates-v1" if self._template_enabled else None
        if provider_id == "fake_planner":
            return "forgex-fake-planner-v1" if self._fake_enabled else None
        if provider_id not in self._model_provider_registry.provider_ids():
            return None
        return self._selected_model(provider_id) or None

    def workflow_route_metadata(
        self, provider_id: str, *, fallback_provider_id: str | None = None, model_id: str | None = None
    ) -> dict[str, object]:
        # ``fallback_provider_id`` is retained only for wire compatibility.
        # Model Runtime V2 never changes provider/model automatically.
        del fallback_provider_id
        if provider_id == "verified_template":
            if not self._template_enabled:
                raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
            return {
                "provider_id": provider_id,
                "model_id": "forgex-verified-templates-v1",
                "fallback_enabled": False,
                "fallback_provider_id": None,
                "local_only": True,
            }
        if provider_id == "fake_planner":
            if not self._fake_enabled:
                raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
            return {
                "provider_id": provider_id,
                "model_id": "forgex-fake-planner-v1",
                "fallback_enabled": False,
                "fallback_provider_id": None,
                "local_only": True,
            }
        if provider_id not in self._model_provider_registry.provider_ids():
            raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")
        provider = self._model_provider_registry.provider(provider_id)
        model_id = model_id or self._selected_model(provider_id)
        if not provider.enabled:
            raise ValueError("API_PROVIDER_DISABLED")
        if not provider.configured:
            raise ValueError("API_PROVIDER_KEY_MISSING")
        if not model_id:
            raise ValueError("API_PROVIDER_MODEL_MISSING")
        return {
            "provider_id": provider_id,
            "model_id": model_id,
            "fallback_enabled": False,
            "fallback_provider_id": None,
            "local_only": provider.local,
        }

    def configured_fallback(self, provider_id: str) -> str | None:
        del provider_id
        return None

    def fallback_for_instruction(self, provider_id: str, instruction: str) -> str | None:
        del provider_id, instruction
        return None

    def route_snapshot(self, provider_id: str) -> dict[str, object]:
        provider = self._model_provider_registry.provider(provider_id)
        return {
            "provider_id": provider_id,
            "model_id": self._selected_model(provider_id),
            "fallback_enabled": False,
            "fallback_provider_id": None,
            "local_only": provider.local,
        }

    def _selected_model(self, provider_id: str) -> str:
        route = self._model_provider_registry.route_for_task("code_generation")
        if route.provider_id == provider_id and route.model_id:
            return route.model_id
        return self._model_provider_registry.default_model(provider_id)
