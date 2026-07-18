"""Cerebras OpenAI-compatible provider adapter."""

from .openai_provider import OpenAIProvider


class CerebrasProvider(OpenAIProvider):
    provider_id = "cerebras"
