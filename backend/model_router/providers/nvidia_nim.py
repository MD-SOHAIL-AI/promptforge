"""NVIDIA NIM OpenAI-compatible provider adapter."""

from .openai_provider import OpenAIProvider


class NvidiaNimProvider(OpenAIProvider):
    provider_id = "nvidia_nim"
