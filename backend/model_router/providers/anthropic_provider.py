"""Anthropic API provider adapter."""

from __future__ import annotations

from ...services.llm_service import AnthropicService
from .openai_provider import OpenAIProvider


class AnthropicProvider(OpenAIProvider):
    provider_id = "anthropic"
    service_type = AnthropicService
