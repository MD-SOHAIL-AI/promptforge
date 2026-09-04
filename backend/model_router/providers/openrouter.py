"""OpenRouter provider adapter."""

from __future__ import annotations

from dataclasses import replace

from ...services.llm_service import (
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    OpenRouterService,
)
from ..models import ModelRequest, ModelResponse
from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    provider_id = "openrouter"
    service_type = OpenRouterService

    async def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            return await super().generate(request)
        except (LLMProviderError, LLMRateLimitError, LLMResponseError) as exc:
            if not should_try_free_fallback(request.model_id, exc):
                raise
            original = exc

        for model_id in self._free_fallback_model_ids(request.model_id):
            try:
                return await super().generate(replace(request, model_id=model_id))
            except (LLMProviderError, LLMRateLimitError, LLMResponseError) as exc:
                if not should_try_free_fallback(model_id, exc):
                    raise
                continue
        raise original

    def _free_fallback_model_ids(self, failed_model_id: str | None) -> tuple[str, ...]:
        failed = (failed_model_id or "").strip()
        preferred = (
            "openai/gpt-oss-20b:free",
            "cohere/north-mini-code:free",
            "poolside/laguna-xs-2.1:free",
        )
        ordered: list[str] = []
        for model_id in preferred:
            if model_id != failed:
                ordered.append(model_id)
        for model in self.registry.list_models(self.provider_id):
            model_id = model.model_id.strip()
            if not model_id or model_id == failed or model_id in ordered:
                continue
            if model.free is True or model_id.endswith(":free"):
                ordered.append(model_id)
        return tuple(ordered[:5])


def should_try_free_fallback(
    model_id: str | None,
    exc: LLMProviderError | LLMRateLimitError | LLMResponseError,
) -> bool:
    if not model_id or not model_id.endswith(":free"):
        return False
    if isinstance(exc, LLMRateLimitError):
        return True
    if isinstance(exc, LLMResponseError):
        return True
    if exc.retryable:
        return True
    message = exc.message.casefold()
    return "unavailable" in message or "not available" in message
