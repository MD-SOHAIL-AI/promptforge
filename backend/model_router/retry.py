from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import random
import math
import re
from typing import Any, Mapping

from ..services.llm_service import LLMError, LLMRateLimitError, LLMTimeoutError, LLMProviderError, LLMConfigurationError, LLMAuthenticationError

# Ported from the supplied OpenCode 1.18.34 session/retry.ts.
RETRY_MAX_RETRIES = 5
RETRY_MAX_DELAY = 2_147_483_647
_TRANSIENT = re.compile(r"429|500|502|503|504|524|rate[ _-]?limit|too many requests|overloaded|service[ _-]unavailable|internal[ _-](?:server[ _-])?error|server[ _-]error|provider[ _-]returned[ _-]error|terminated|fetch failed|failed to fetch|network[ _-]error|upstream connect|connection (?:error|refused|lost)|socket hang up|reset before headers|getaddrinfo|enotfound|eai_again|econnrefused|econnreset|etimedout|^timeout$|\b(?:request|response|connection|network|stream|read) (?:timeout|timed out|time out)\b|try your request again|retry your request|resource[ _]exhausted|try again (?:later|in\b)|(?:currently|temporarily) at capacity", re.I)


def retry_after_ms_from_details(details: Mapping[str, Any] | None) -> int | None:
    if not details:
        return None
    headers = details.get("response_headers") or details.get("responseHeaders") or {}
    details = {**({str(k).lower(): v for k, v in headers.items()} if isinstance(headers, Mapping) else {}),
               **{str(k).lower(): v for k, v in details.items() if v is not None}}
    for key in ("retry_after_ms", "retry-after-ms"):
        value = details.get(key)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)
            if math.isfinite(number) and number >= 0:
                return min(RETRY_MAX_DELAY, math.ceil(number))
        except (TypeError, ValueError, OverflowError):
            pass
    for key in ("retry_after", "retry-after", "Retry-After"):
        value = details.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
            return min(RETRY_MAX_DELAY, math.ceil(float(value) * 1000))
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                number = float(text)
                if math.isfinite(number) and number >= 0:
                    return min(RETRY_MAX_DELAY, math.ceil(number * 1000))
            except (ValueError, OverflowError):
                try:
                    when = parsedate_to_datetime(text)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    delay = int((when - datetime.now(timezone.utc)).total_seconds() * 1000)
                    if delay > 0:
                        return min(RETRY_MAX_DELAY, delay)
                except (TypeError, ValueError, OverflowError):
                    pass
    return None


def exponential_backoff_ms(
    attempt: int,
    *,
    base_delay_ms: int = 2_000,
    max_delay_ms: int = 30_000,
    jitter_ratio: float = 0.25,
    random_value: float | None = None,
) -> int:
    """Return bounded exponential backoff for a one-based failed attempt.

    Jitter prevents several queued runs from retrying an overloaded endpoint at
    exactly the same time. ``random_value`` exists so policy tests can remain
    deterministic without weakening production behavior.
    """

    exponent = min(30, max(0, int(attempt) - 1))
    bounded = max(0, int(base_delay_ms)) * (2**exponent)
    ratio = max(0.0, min(float(jitter_ratio), 1.0))
    sample = random.random() if random_value is None else max(0.0, min(float(random_value), 1.0))
    multiplier = 1.0 + ratio * sample
    return max(0, min(int(max_delay_ms), math.ceil(bounded * multiplier)))


def transient_retry_delay_ms(
    exc: Exception,
    attempt: int,
    *,
    base_delay_ms: int = 2_000,
    max_delay_ms: int = 30_000,
) -> int:
    """Honor a provider reset hint, otherwise use bounded jittered backoff."""

    retry_after = retry_after_ms_from_details(getattr(exc, "details", None))
    if retry_after is not None:
        return retry_after
    return exponential_backoff_ms(
        attempt,
        base_delay_ms=base_delay_ms,
        max_delay_ms=RETRY_MAX_DELAY if getattr(exc, "details", {}).get("response_headers") is not None else max_delay_ms,
    )


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    delay_ms: int = 0
    reason: str = "not_retryable"


class RetryPolicy:
    def __init__(self, *, max_attempts: int = RETRY_MAX_RETRIES + 1, max_rate_limit_delay_ms: int = 30_000) -> None:
        self.max_attempts = max(1, max_attempts)
        self.max_rate_limit_delay_ms = max(0, max_rate_limit_delay_ms)

    def decide(self, exc: Exception, attempt: int) -> RetryDecision:
        if attempt >= self.max_attempts:
            return RetryDecision(False, reason="attempt_limit")
        details = getattr(exc, "details", {})
        status = getattr(exc, "status_code", None)
        if isinstance(exc, (LLMConfigurationError, LLMAuthenticationError)):
            return RetryDecision(False)
        if details.get("category") in {"configuration", "authentication", "quota_exceeded", "context_overflow", "content_policy", "cancelled"} or status in {401, 403}:
            return RetryDecision(False)
        if details.get("origin") in {"provider_circuit", "provider_admission", "run_budget", "verification_reserve"}:
            return RetryDecision(False)
        if str(getattr(exc, "code", "")) in {"CONTEXT_OVERFLOW", "MODEL_CAPABILITY_MISMATCH", "PROVIDER_REQUEST_ATTEMPTS_EXHAUSTED", "AGENT_BUDGET_EXCEEDED", "MODEL_OUTPUT_EMPTY", "TOOL_ARGUMENTS_INVALID"}:
            return RetryDecision(False)
        if isinstance(exc, LLMError) and (isinstance(exc, (LLMTimeoutError, LLMRateLimitError)) or getattr(exc, "retryable", False) or status is not None and status >= 500 or _TRANSIENT.search(str(exc))):
            delay = transient_retry_delay_ms(exc, attempt)
            if delay > self.max_rate_limit_delay_ms:
                return RetryDecision(False, delay, "durable_wait")
            return RetryDecision(True, delay, "same_model_retry")
        return RetryDecision(False)
