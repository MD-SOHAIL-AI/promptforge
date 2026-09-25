from __future__ import annotations

from dataclasses import dataclass

from ..services.llm_service import LLMConfigurationError, LLMProvider, LLMProviderError
from .catalog import ModelCatalog
from .models import ModelCapabilities, ModelRequest, RouteRequest
from .protocols import ProtocolRegistry
from .registry import ProviderRegistry
from .selection import ModelSelectionStore


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    provider_id: str
    model_id: str
    protocol: str
    capabilities: ModelCapabilities
    variant: str | None = None
    selection_source: str = "task_route"


class ModelResolver:
    def __init__(self, registry: ProviderRegistry, catalog: ModelCatalog | None = None, selections: ModelSelectionStore | None = None) -> None:
        self.registry = registry
        self.catalog = catalog or ModelCatalog(registry)
        self.protocols = ProtocolRegistry(registry)
        self.selections = selections

    def resolve(self, request: ModelRequest, *, streaming: bool = False) -> ResolvedModel:
        route = self.registry.route_for_task(request.task_type)
        selected = None
        source = "explicit" if request.provider_id or request.model_id else "task_route"
        if self.selections is not None and not (request.provider_id or request.model_id):
            selected, source = self.selections.resolve(
                session_id=request.metadata.get("session_id"),
                role=str(request.metadata.get("agent_role") or "main"),
            )
            if selected is None:
                source = "task_route"
        provider_id = request.provider_id or (selected.provider_id if selected else route.provider_id)
        model_id = self.registry.resolve_model_id(
            provider_id,
            request.model_id or (selected.model_id if selected else route.model_id if provider_id == route.provider_id else self.registry.default_model(provider_id)),
        )
        provider = self.registry.provider(provider_id)
        if (request.local_only or route.local_only) and not provider.local:
            raise LLMConfigurationError(
                "The selected provider is not local.",
                provider=LLMProvider.OPENROUTER,
                code="MODEL_ROUTE_LOCAL_ONLY",
                details={"provider_id": provider_id, "model_id": model_id},
            )
        if not provider.configured or not provider.enabled:
            raise LLMConfigurationError(
                f"Model provider {provider_id} is not configured or enabled.",
                provider=LLMProvider.OPENROUTER,
                code="MODEL_PROVIDER_UNAVAILABLE",
                details={"provider_id": provider_id, "model_id": model_id},
            )
        caps = self.catalog.capabilities(provider_id, model_id)
        structured = request.structured_output
        requirements = RouteRequest(
            task_type=request.task_type,
            provider_id=provider_id,
            model_id=model_id,
            allow_fallback=False,
            local_only=request.local_only,
            require_text_generation=True,
            require_streaming=streaming,
            require_json_schema=bool(structured and structured.strict and not structured.allow_prompt_fallback),
        )
        if not caps.satisfies(requirements):
            raise LLMProviderError(
                f"Model {provider_id}/{model_id} does not satisfy the requested capabilities.",
                provider=LLMProvider.OPENROUTER,
                code="MODEL_CAPABILITY_MISMATCH",
                retryable=False,
                details={"provider_id": provider_id, "model_id": model_id, "capabilities": caps.to_dict()},
            )
        protocol = self.protocols.resolve(provider_id)
        return ResolvedModel(provider_id, model_id, protocol.protocol, caps, selection_source=source)
