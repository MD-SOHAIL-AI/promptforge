from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
import random
from typing import Any, Mapping

from ..services.llm_service import LLMError, LLMRateLimitError, LLMTimeoutError, LLMProviderError


def retry_after_ms_from_details(details: Mapping[str, Any] | None) -> int | None:
    if not details:
        return None
    for key in ("retry_after_ms", "retry-after-ms"):
        value = details.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    for key in ("retry_after", "retry-after", "Retry-After"):
        value = details.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return int(float(value) * 1000)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            try:
                return max(0, int(float(text) * 1000))
            except ValueError:
                try:
                    when = parsedate_to_datetime(text)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    return max(0, int((when - datetime.now(timezone.utc)).total_seconds() * 1000))
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

    exponent = max(0, int(attempt) - 1)
    bounded = min(max(0, int(max_delay_ms)), max(0, int(base_delay_ms)) * (2**exponent))
    ratio = max(0.0, min(float(jitter_ratio), 1.0))
    sample = random.random() if random_value is None else max(0.0, min(float(random_value), 1.0))
    multiplier = (1.0 - ratio) + ((2.0 * ratio) * sample)
    return max(0, min(int(max_delay_ms), int(round(bounded * multiplier))))


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
        return max(0, min(int(max_delay_ms), retry_after))
    return exponential_backoff_ms(
        attempt,
        base_delay_ms=base_delay_ms,
        max_delay_ms=max_delay_ms,
    )


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retry: bool
    delay_ms: int = 0
    reason: str = "not_retryable"


class RetryPolicy:
    def __init__(self, *, max_attempts: int = 2, max_rate_limit_delay_ms: int = 5_000) -> None:
        self.max_attempts = max(1, max_attempts)
        self.max_rate_limit_delay_ms = max(0, max_rate_limit_delay_ms)

    def decide(self, exc: Exception, attempt: int) -> RetryDecision:
        if attempt >= self.max_attempts:
            return RetryDecision(False, reason="attempt_limit")
        if isinstance(exc, LLMRateLimitError):
            delay = retry_after_ms_from_details(getattr(exc, "details", None))
            if delay is None:
                return RetryDecision(False, reason="rate_limit_no_reset")
            if delay > self.max_rate_limit_delay_ms:
                return RetryDecision(False, delay, "rate_limit_long_reset")
            return RetryDecision(True, delay, "rate_limit_short_reset")
        if isinstance(exc, LLMTimeoutError):
            return RetryDecision(True, 0, "timeout")
        if isinstance(exc, LLMProviderError):
            status = getattr(exc, "status_code", None)
            if status in {502, 503, 504} or getattr(exc, "retryable", False):
                return RetryDecision(True, 0, "provider_transient")
        if isinstance(exc, LLMError) and getattr(exc, "retryable", False):
            return RetryDecision(True, 0, "transient")
        return RetryDecision(False)
