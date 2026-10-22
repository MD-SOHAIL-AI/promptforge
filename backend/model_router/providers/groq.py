"""Groq OpenAI-compatible API provider adapter."""

from .openai_provider import OpenAIProvider


class GroqProvider(OpenAIProvider):
    provider_id = "groq"
