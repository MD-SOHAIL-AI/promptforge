"""OpenRouter provider adapter."""

from __future__ import annotations

from ...services.llm_service import OpenRouterService
from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    provider_id = "openrouter"
    service_type = OpenRouterService
