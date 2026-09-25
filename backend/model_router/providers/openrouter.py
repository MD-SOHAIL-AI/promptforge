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
        """Stream only the explicitly selected OpenRouter model.

        Before the first visible token, bounded same-model retries are allowed
        for transient failures. Once output is visible, errors surface
        immediately so callers never receive a duplicated partial response.
        """
        last_error = None
        for _attempt in range(OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS):
            emitted = False
            try:
                async with aclosing(super().generate_stream(replace(request, allow_fallback=False))) as stream:
                    try:
                        first = await asyncio.wait_for(anext(stream), timeout=OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS)
                    except StopAsyncIteration as exc:
                        recovered = await super().generate(replace(request, allow_fallback=False))
                        if not recovered.content.strip():
                            raise LLMResponseError(
                                "OPENROUTER returned an empty streaming response",
                                provider=LLMProvider.OPENROUTER,
                            ) from exc
                        emitted = True
                        yield recovered.content
                        return
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        raise LLMTimeoutError(
                            "OPENROUTER did not produce a first token in time",
                            provider=LLMProvider.OPENROUTER, retryable=True,
                            details={"operation": "first_token", "timeout_s": OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS},
                        ) from exc
                    emitted = True
                    yield first
                    async for chunk in stream:
                        yield chunk
                return
            except (LLMProviderError, LLMRateLimitError, LLMResponseError, LLMTimeoutError) as exc:
                last_error = exc
                if (
                    emitted
                    or _attempt >= OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS - 1
                    or not getattr(exc, "retryable", False)
                ):
                    raise
                await asyncio.sleep(min(0.25 * (2 ** _attempt), 1.0))
        if last_error is not None:
            raise last_error

    async def generate_event_stream(self, request: ModelRequest):
        """Stream canonical events with transactional Agent retries.

        Planning streams are buffered until the provider sends a complete
        response. This prevents a partial text/reasoning delta from disabling
        a safe same-model retry or contaminating the next attempt's tool-call
        assembly. Chat streams remain live and are never replayed after a
        visible delta.
        """

        last_error = None
        transactional = _is_transactional_agent_stream(request)
        for attempt in range(OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS):
            emitted = False
            buffered: list[CanonicalStreamEvent] = []
            try:
                async with aclosing(
                    super().generate_event_stream(
                        replace(request, allow_fallback=False)
                    )
                ) as stream:
                    try:
                        first = await asyncio.wait_for(
                            anext(stream),
                            timeout=OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS,
                        )
                    except StopAsyncIteration as exc:
                        raise LLMResponseError(
                            "OPENROUTER returned an empty canonical stream",
                            provider=LLMProvider.OPENROUTER,
                            code="PROVIDER_STREAM_EMPTY",
                            retryable=True,
                        ) from exc
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        raise LLMTimeoutError(
                            "OPENROUTER did not produce a first event in time",
                            provider=LLMProvider.OPENROUTER,
                            retryable=True,
                            details={
                                "operation": "first_event",
                                "timeout_s": OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS,
                            },
                        ) from exc
                    if transactional:
                        buffered.append(first)
                    else:
                        emitted = True
                        yield first
                    async for event in stream:
                        if transactional:
                            buffered.append(event)
                            if len(buffered) > OPENROUTER_TRANSACTIONAL_STREAM_MAX_EVENTS:
                                raise LLMResponseError(
                                    "OPENROUTER Agent stream exceeded the safe event buffer",
                                    provider=LLMProvider.OPENROUTER,
                                    code="PROVIDER_STREAM_TOO_LARGE",
                                    retryable=False,
                                )
                        else:
                            yield event
                if transactional:
                    for event in buffered:
                        emitted = True
                        yield event
                return
            except (
                LLMProviderError,
                LLMRateLimitError,
                LLMResponseError,
                LLMTimeoutError,
            ) as exc:
                last_error = exc
                if (
                    emitted
                    or attempt >= OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS - 1
                    or not getattr(exc, "retryable", False)
                ):
                    raise
                retry_delay = min(0.25 * (2 ** attempt), 1.0)
                emit_diagnostic_event(
                    component="provider",
                    event="provider.stream.retrying",
                    level="WARNING",
                    provider=self.provider_id,
                    model=request.model_id,
                    run_id=_metadata_text(request, "run_id"),
                    turn_id=_metadata_text(request, "turn_id"),
                    attempt=attempt + 1,
                    next_attempt=attempt + 2,
                    max_attempts=OPENROUTER_STREAM_MAX_MODEL_ATTEMPTS,
                    error_code=str(getattr(exc, "code", type(exc).__name__)),
                    status=getattr(exc, "status_code", None),
                    retryable=True,
                    transactional=transactional,
                    delay_ms=int(retry_delay * 1000),
                )
                await asyncio.sleep(retry_delay)
        if last_error is not None:
            raise last_error

    def _free_fallback_model_ids(self, failed_model_id: str | None) -> tuple[str, ...]:
        return openrouter_free_fallback_model_ids(failed_model_id, self.registry.list_models(self.provider_id))


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
