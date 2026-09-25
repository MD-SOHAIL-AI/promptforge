"""Anthropic Messages API provider adapter."""

from ...services.llm_service import AnthropicService
from .openai_provider import OpenAIProvider


class AnthropicProvider(OpenAIProvider):
    provider_id = "anthropic"
    service_type = AnthropicService
