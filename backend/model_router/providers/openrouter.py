"""OpenRouter provider adapter."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import replace

from backend.observability.diagnostics import emit_diagnostic_event

from ...services.llm_service import (
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    OpenRouterService,
)
from ..models import ModelRequest, ModelResponse
from ..models import CanonicalStreamEvent
from .openai_provider import OpenAIProvider


OPENROUTER_FREE_FALLBACK_MODELS = (
    "cohere/north-mini-code:free",
    "poolside/laguna-xs-2.1:free",
)
OPENROUTER_KNOWN_UNAVAILABLE_FREE_MODELS = frozenset({
    "openai/gpt-oss-20b:free",
})
OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS = 15.0
OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS = 3
OPENROUTER_TRANSACTIONAL_STREAM_MAX_EVENTS = 50_000


def openrouter_free_fallback_model_ids(failed_model_id: str | None, models: object = ()) -> tuple[str, ...]:
    """Compatibility helper retained for older callers.

    Automatic OpenRouter model rotation is disabled in Model Runtime V2.
    """
    del failed_model_id, models
    return ()


class OpenRouterProvider(OpenAIProvider):
    provider_id = "openrouter"
    service_type = OpenRouterService

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await super().generate(replace(request, allow_fallback=False))

    async def generate_stream(self, request: ModelRequest):
        from ..request_recovery import text_stream
        async with aclosing(text_stream(request, super().generate_stream, super().generate,
            first_token_timeout=OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS)) as source:
            async for event in source:
                yield event


def should_try_free_fallback(
    model_id: str | None,
    exc: LLMProviderError | LLMRateLimitError | LLMResponseError | LLMTimeoutError,
) -> bool:
    if not model_id or not model_id.endswith(":free"):
        return False
    if isinstance(exc, LLMRateLimitError):
        return True
    if isinstance(exc, LLMResponseError):
        return True
    if isinstance(exc, LLMTimeoutError):
        return True
    if exc.status_code in {400, 404, 502, 503}:
        return True
    if exc.retryable:
        return True
    message = exc.message.casefold()
    return "unavailable" in message or "not available" in message


def _is_transactional_agent_stream(request: ModelRequest) -> bool:
    return request.task_type == "planning" or bool(
        request.metadata.get("forgex_native_tools")
    )


def _metadata_text(request: ModelRequest, key: str) -> str | None:
    value = request.metadata.get(key)
    return value if isinstance(value, str) and value.strip() else None
