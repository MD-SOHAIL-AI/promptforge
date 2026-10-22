"""Runtime adapter for user-defined protocol routes."""

from __future__ import annotations

from ...services.llm_service import AnthropicService, OpenAIResponsesService, OpenAIService
from .openai_provider import OpenAIProvider


class CompatibleProvider(OpenAIProvider):
    def __init__(self, registry, provider_id: str, protocol: str) -> None:
        self.provider_id = provider_id
        self.service_type = {
            "openai-completions": OpenAIService,
            "openai-responses": OpenAIResponsesService,
            "anthropic-messages": AnthropicService,
        }[protocol]
        super().__init__(registry)

    def _service(self, model_id: str | None = None):
        definition = self.registry.definition(self.provider_id)
        api_key = self.registry.api_key(self.provider_id)
        if not api_key and not definition.local:
            raise ValueError(
                f"{definition.display_name} is not configured. Add your API key in Models."
            )
        return self.service_type(
            api_key=api_key or "local-provider",
            model=model_id or self.registry.default_model(self.provider_id),
            base_url=self.registry.base_url(self.provider_id),
        )
