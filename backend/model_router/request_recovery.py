"""One bounded recovery chain per logical agent request, including repair.

Hooks remain local to the router. Scheduled retries are journaled before waits.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from datetime import datetime, timedelta, timezone

from backend.observability.diagnostics import emit_diagnostic_event
from .transport_policy import request_transport_policy
from backend.services.llm_service import LLMError, LLMResponseError, LLMProvider, LLMRateLimitError
from backend.services.llm_service import LLMTimeoutError
from .retry import RetryPolicy, retry_after_ms_from_details, transient_retry_delay_ms
from .models import execution_value


def managed(request):
    return execution_value(request, "managed", "forgex_managed_recovery", False) is True


def remaining(request):
    state = execution_value(request, "attempts", "forgex_request_attempts")
    return max(0, int(state["limit"]) - int(state["used"])) if isinstance(state, dict) else 6


async def publish(request, phase, **fields):
    payload = {"event_type": "model.retry." + phase,
               "run_id": request.metadata.get("run_id"),
               "turn_id": request.metadata.get("turn_id"),
               "provider": request.provider_id, "model": request.model_id,
               "provider_id": request.provider_id, "model_id": request.model_id, **fields}
    hook = execution_value(request, "recovery_hook", "forgex_recovery_hook")
    if callable(hook):
        result = hook(payload)
        if inspect.isawaitable(result):
            await result
    emit_diagnostic_event(component="provider", event=payload["event_type"], **{key: value for key, value in payload.items() if key != "event_type"})


async def retry_wait(request, exc, attempt):
    """Return False to surface the original error; never shorten Retry-After."""
    if not managed(request) or os.getenv("FORGEX_ENABLE_REQUEST_RECOVERY", "1") != "1":
        return False
    if getattr(exc, "details", {}).get("origin") == "provider_circuit" or getattr(exc, "details", {}).get("circuit_open"):
        return False
    details = getattr(exc, "details", {})
    # A long silent/stalled request has already spent a useful response window.
    # Release it to persisted job recovery instead of immediately repeating it.
    if isinstance(exc, LLMTimeoutError) and (
        details.get("timeout_stage") in {"first_event", "stream_idle", "attempt_total", "job_deadline", "turn_deadline"}
        and (details.get("duration_ms", 0) >= 30_000 or details.get("timeout_stage") in {"job_deadline", "turn_deadline"})
    ):
        return False
    state = execution_value(request, "attempts", "forgex_request_attempts", {})
    max_attempts = min(6, int(state.get("limit", 6)))
    if not remaining(request) or attempt >= max_attempts:
        return False
    if getattr(exc, "code", None) == "PROVIDER_STREAM_INVALID" and attempt >= 3:
        return False  # Two incomplete-stream retries per primary/repair sequence.
    policy = RetryPolicy(max_attempts=max_attempts, max_rate_limit_delay_ms=30_000)
    decision = policy.decide(exc, attempt)
    # Retry an interrupted response only while all events are still private.
    response_retry = isinstance(exc, LLMResponseError) and getattr(exc, "retryable", False)
    rate_retry = isinstance(exc, LLMRateLimitError) and retry_after_ms_from_details(getattr(exc, "details", None)) is None
    if not decision.retry and not response_retry and not rate_retry:
        return False
    hint = retry_after_ms_from_details(getattr(exc, "details", None))
    delay = hint if hint is not None else transient_retry_delay_ms(exc, attempt)
    if delay > 30_000:
        return False  # Durable job recovery owns longer waits.
    turn_deadline = execution_value(request, "deadline", "forgex_turn_deadline")
    if isinstance(turn_deadline, (int, float)) and not isinstance(turn_deadline, bool):
        # Do not reserve and launch a retry that cannot get a useful response
        # before the parent turn times out. Durable recovery owns the next try.
        useful_window = request_transport_policy(request).minimum_response_seconds
        if time.monotonic() + delay / 1000 + useful_window >= turn_deadline:
            return False
    state = execution_value(request, "attempts", "forgex_request_attempts", {})
    fields = {"attempt": int(state.get("used", attempt)), "max_attempts": int(state.get("limit", 6)),
              "delay_ms": delay, "error_code": str(getattr(exc, "code", type(exc).__name__)),
              "retry_due_at": (datetime.now(timezone.utc) + timedelta(milliseconds=delay)).isoformat(),
              "http_status": getattr(exc, "status_code", None),
              "origin": getattr(exc, "details", {}).get("origin") or "provider",
              "request_reached_provider": getattr(exc, "details", {}).get("request_reached_provider")}
    await publish(request, "scheduled", **fields)
    try:
        await asyncio.sleep(delay / 1000)
    except asyncio.CancelledError:
        await publish(request, "cancelled", **fields)
        raise
    await publish(request, "started", **fields)
    return True


async def agent_stream(request, stream):
    """Release one complete attempt, so a broken stream cannot duplicate tools."""
    for attempt in range(1, 7):
        events = []
        size = 0
        try:
            from contextlib import aclosing
            async with aclosing(stream(request)) as source:
                async for event in source:
                    size += len(str(event))
                    if len(events) >= 50_000 or size > 8_000_000:
                        raise LLMResponseError("Agent stream exceeded its bounded buffer", provider=LLMProvider.OPENROUTER,
                                               code="PROVIDER_STREAM_TOO_LARGE", retryable=False)
                    events.append(event)
        except LLMError as exc:
            if await retry_wait(request, exc, attempt):
                continue
            raise
        else:
            for event in events:
                yield event
            return


async def public_stream(request, stream):
    """Retry failures before visible output; never replay text or tool events."""
    from contextlib import aclosing
    state = execution_value(request, "attempts", "forgex_request_attempts", {})
    policy = RetryPolicy(max_attempts=min(6, int(state.get("limit", 6))))
    for attempt in range(1, policy.max_attempts + 1):
        emitted = False
        try:
            async with aclosing(stream(request)) as source:
                async for event in source:
                    emitted = True
                    yield event
            return
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            decision = policy.decide(exc, attempt)
            deadline = execution_value(request, "deadline", "forgex_turn_deadline")
            if emitted or not decision.retry or not remaining(request) or isinstance(deadline, (float, int)) and time.monotonic() + decision.delay_ms / 1000 >= deadline:
                raise
            await publish(request, "scheduled", attempt=attempt, max_attempts=policy.max_attempts,
                          delay_ms=decision.delay_ms, error_code=exc.code)
            await asyncio.sleep(decision.delay_ms / 1000)


async def text_stream(request, stream, generate, *, first_token_timeout=None):
    """Compatibility text streams share retry policy and never replay visible text."""
    from dataclasses import replace
    from contextlib import aclosing
    from .models import RequestExecutionContext
    from backend.services.llm_service import LLMTimeoutError
    if request.execution_context is None:
        request = replace(request, execution_context=RequestExecutionContext(
            budget=request.metadata.get("forgex_run_budget", {}),
            attempts=request.metadata.get("forgex_request_attempts", {"used": 0, "limit": 6}),
            diagnostics=request.metadata.get("forgex_attempt_diagnostics", {}),
            recovery_hook=request.metadata.get("forgex_recovery_hook"),
            deadline=request.metadata.get("forgex_turn_deadline"),
            deadline_kind="turn_deadline" if request.metadata.get("forgex_turn_deadline") is not None else "job_deadline",
            transport_policy=request.metadata.get("forgex_transport_policy")))
    if first_token_timeout is None:
        first_token_timeout = request_transport_policy(request).minimum_response_seconds
    for attempt in range(1, 7):
        emitted = False
        try:
            async with aclosing(stream(request)) as events:
                try:
                    first = await asyncio.wait_for(anext(events), timeout=first_token_timeout)
                except StopAsyncIteration:
                    answer = await generate(request)
                    if not answer.content.strip():
                        raise LLMResponseError("The provider returned an empty response", provider=LLMProvider.OPENROUTER, retryable=False)
                    yield answer.content
                    return
                except asyncio.TimeoutError as exc:
                    raise LLMTimeoutError("The provider did not return a first token in time", provider=LLMProvider.OPENROUTER, retryable=True) from exc
                emitted = True
                yield first
                async for event in events:
                    yield event
            return
        except LLMError as exc:
            if emitted or not await retry_wait(request, exc, attempt):
                raise
