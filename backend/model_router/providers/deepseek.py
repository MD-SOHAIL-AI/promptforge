"""DeepSeek OpenAI-compatible API provider adapter."""

from ...services.llm_service import OpenAIService
from .openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    provider_id = "deepseek"
    service_type = OpenAIService
