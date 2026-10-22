"""Gemini API provider adapter."""

from __future__ import annotations

from ...services.llm_service import GeminiService
from .openai_provider import OpenAIProvider


class GeminiProvider(OpenAIProvider):
    provider_id = "gemini"
    service_type = GeminiService
