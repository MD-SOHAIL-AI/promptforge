"""
runtime/retry.py — Retry semantics with structured failure classification.

Design goals:
- Every retry decision is explicit and auditable. No hidden magic.
- Backoff uses full-jitter to prevent correlated retries across sessions
  when multiple devices fail simultaneously (the "thundering herd" problem).
- Retry predicates are composable: combine exception type checks, exit code
  checks, and custom heuristics into a single policy object.
- The engine distinguishes three outcomes after a failure:
    RETRY    — try the same stage again, same plan
    ESCALATE — restart from an earlier stage (e.g., full replan after
               repeated build failures, meaning the plan itself was bad)
    ABORT    — no further attempts; transition session to FAILED

Backoff formula (full-jitter variant):
    delay = random.uniform(0, min(cap, base * factor^attempt))

This has better collision-avoidance than "equal jitter" or "decorrelated jitter"
when many sessions retry simultaneously. See AWS architecture blog for analysis.

──────────────────────────────────────────────────────────────────────────────
HARDENING CHANGES vs. ORIGINAL
──────────────────────────────────────────────────────────────────────────────

[FIX-R1] Structured failure classification replaces raw string heuristics.
  FailureCategory enum + classify_failure() give typed, deterministic,
  stage-aware retry decisions. The original is_transient_error() matched
  "no such file or directory" as transient — this fires when arm-none-eabi-gcc
  is missing (a deterministic, non-retryable failure) and would cause infinite
  retries burning through the full retry budget on an unfixable error.

[FIX-R2] Retry budget no longer consumed by policy-ABORT decisions.
  When the exception is non-retryable by category (e.g. BUILD_ERROR,
  TOOLCHAIN_MISSING), the ABORT decision is recorded in history for
  auditability but does NOT increment the retry counter. This preserves
  the retry budget for genuine transient failures in later stages.

[FIX-R3] wait() tracks _total_wait_s AFTER the sleep, not before.
  If asyncio.CancelledError interrupts the sleep, the previous code
  recorded the full planned delay even though the runtime only waited
  a fraction of it. Now we track actual elapsed wait time via try/finally.

[FIX-R4] is_transient_error() and is_build_error() now delegate to
  classify_failure() for consistency. They remain in the public API
  for backward compatibility with existing callers.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional, TYPE_CHECKING

# FIX #1: Import the single authoritative FailureCategory / FailureSeverity /
# FailureClassification from failure_classifier.py.  The duplicate definitions
# that previously lived in this file have been removed.
from .failure_classifier import (
    FailureCategory,
    FailureSeverity,
    FailureClassification,
    classify_failure as _fc_classify_failure,
)

if TYPE_CHECKING:
    from .session import Session


__all__ = [
    "FailureCategory",
    "classify_failure",
    "RetryOutcome",
    "RetryPolicy",
    "RetryDecision",
    "RetryEngine",
    "RetryExhaustedError",
    "is_transient_error",
    "is_build_error",
]


# ── Canonical retry verdict per FailureCategory ────────────────────────────────
# failure_classifier.py already embeds a `retryable` flag on each
# FailureClassification; this table is the retry engine's policy override layer.
# True  = this category is eligible for retry (subject to attempt budget).
# False = ABORT immediately; retrying cannot resolve this class of failure.
_CATEGORY_RETRYABLE: dict[FailureCategory, bool] = {
    FailureCategory.BUILD_ERROR:              False,
    FailureCategory.COMPILATION_ERROR:        False,
    FailureCategory.LINKER_ERROR:             False,
    FailureCategory.TOOLCHAIN_MISSING:        False,
    FailureCategory.INVALID_FIRMWARE:         False,
    FailureCategory.FLASH_TIMEOUT:            True,
    FailureCategory.DEVICE_DISCONNECTED:      True,
    FailureCategory.PORT_BUSY:                True,
    FailureCategory.TRANSIENT_USB_FAILURE:    True,
    FailureCategory.SERIAL_PERMISSION_ERROR:  False,
    FailureCategory.OBSERVE_TIMEOUT:          True,
    FailureCategory.WATCHDOG_RESET:           True,
    FailureCategory.SERIAL_FRAMING_ERROR:     True,
    FailureCategory.UNKNOWN:                  True,
}


def classify_failure(exc: Exception, stage: str = "") -> FailureCategory:
    """
    Classify an exception into a deterministic FailureCategory.

    Delegates to failure_classifier.classify_failure() — the single
    authoritative implementation — and extracts the FailureCategory.

    The `stage` parameter is forwarded as the `stage` keyword argument so
    stage-aware pattern tables in failure_classifier.py are applied correctly.

    Backward-compatible wrapper: existing callers in retry.py, engine.py, and
    tests that call ``classify_failure(exc, stage=...)`` continue to work
    without modification.
    """
    # Lazy import to avoid circular dependency at module level.
    # subprocess_mgr types are used only to extract stderr/stdout for richer
    # classification when the exception carries a ProcessResult.
    stderr_text: Optional[str] = None
    stdout_text: Optional[str] = None
    process_result = None

    try:
        from .subprocess_mgr import ExecutionError, ProcessTimeoutError
        if isinstance(exc, (ExecutionError,)):
            process_result = exc.result
        elif isinstance(exc, ProcessTimeoutError):
            # Map timeout exceptions directly using the richer classifier
            pass
    except ImportError:
        pass

    classification = _fc_classify_failure(
        exception=exc,
        stderr=stderr_text,
        stdout=stdout_text,
        process_result=process_result,
        stage=stage,
    )
    return classification.category


# ── Outcome enum ──────────────────────────────────────────────────────────────

class RetryOutcome(Enum):
    RETRY    = auto()   # Same stage, same plan, after backoff
    ESCALATE = auto()   # Roll back to an earlier stage (e.g., replan)
    ABORT    = auto()   # Give up; caller should transition session to FAILED


# ── Policy ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetryPolicy:
    """
    Immutable retry configuration.

    All fields have production-ready defaults. Override only what you need.

    Escalation logic:
        If the same pipeline stage fails `escalate_after_n_stage_failures` times
        in a row, the outcome is ESCALATE rather than RETRY. This catches the case
        where the generated plan itself is broken — re-running the build won't
        help; a new plan is needed.
    """

    # How many total retry/escalate attempts to make (does not count ABORT decisions)
    max_attempts: int = 3

    # Backoff configuration (full-jitter exponential)
    base_delay_s:   float = 0.5    # Floor for the first retry
    backoff_factor: float = 2.0    # Delay doubles each attempt
    max_delay_s:    float = 30.0   # Hard cap regardless of attempt count
    jitter_seed:    str   = "promptforge-retry-v1"

    # Escalation: after N consecutive failures on the SAME stage, escalate
    escalate_after_n_stage_failures: int = 2

    # Exception filter: if set, only retry on these types
    # (empty tuple = use category-based policy from classify_failure)
    retryable_exceptions: tuple[type[Exception], ...] = ()

    # Custom predicate: return True to allow retry, False to abort immediately.
    # Evaluated AFTER the type filter and the category-based policy.
    # If None, the category policy from _CATEGORY_RETRYABLE is the sole arbiter.
    is_retryable: Optional[Callable[[Exception], bool]] = None

    def delay_for(self, attempt: int) -> float:
        """
        Compute the wait duration before attempt N (0-indexed from 0).

        Uses full-jitter: pick uniformly from [0, computed_cap].
        This means early retries can be very fast, which is desirable
        for transient network/port errors.
        """
        cap = self.delay_cap_for(attempt)
        rng = random.Random(f"{self.jitter_seed}:{attempt}")
        return rng.uniform(0, cap)

    def delay_cap_for(self, attempt: int) -> float:
        """Return the deterministic exponential cap used by full-jitter."""
        if attempt < 0:
            attempt = 0
        return min(
            self.max_delay_s,
            self.base_delay_s * (self.backoff_factor ** attempt),
        )

    def allows_retry(self, exc: Exception, stage: str = "") -> bool:
        """
        [FIX-R1] Check whether an exception is eligible for retry.

        Now uses structured failure classification as the primary arbiter,
        with optional type-filter and custom predicate as overrides.
        """
        # Type filter takes priority (explicit allow-list)
        if self.retryable_exceptions:
            if not isinstance(exc, self.retryable_exceptions):
                return False

        # [FIX-R1] Category-based policy: structured, stage-aware
        category = classify_failure(exc, stage=stage)
        if not _CATEGORY_RETRYABLE.get(category, True):
            return False

        # Custom predicate runs last (most specific override)
        if self.is_retryable is not None:
            return self.is_retryable(exc)

        return True


# ── Decision type ─────────────────────────────────────────────────────────────

@dataclass
class RetryDecision:
    """
    Structured output of a single retry evaluation.

    The engine acts on `outcome`. All other fields are for logging and
    debugging — they tell you WHY the decision was made.
    """
    outcome:      RetryOutcome
    attempt:      int              # 0-indexed attempt that just failed
    delay_s:      float            # How long to wait before the next attempt
    reason:       str              # Human-readable explanation
    exception:    Optional[Exception]  = None
    failure_category: Optional[FailureCategory] = None   # [FIX-R1] structured classification
    # For ESCALATE: the stage to jump back to ("plan", "build", etc.)
    rollback_to:  Optional[str]        = None


# ── Exceptions ────────────────────────────────────────────────────────────────

class RetryExhaustedError(Exception):
    """All configured retry attempts have been consumed."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts   = attempts
        self.last_error = last_error
        super().__init__(
            f"Exhausted all {attempts} retry attempts. "
            f"Last error: {type(last_error).__name__}: {last_error}"
        )


# ── Built-in predicate helpers ────────────────────────────────────────────────

def is_transient_error(exc: Exception, stage: str = "") -> bool:
    """
    [FIX-R4] Wrapper maintained for backward compatibility.

    Now delegates to classify_failure() for structured, stage-aware
    classification rather than raw substring matching.

    Previously this matched "no such file or directory" as transient, which
    also fires for missing toolchain binaries — a deterministic failure that
    retrying cannot resolve (TOOLCHAIN_MISSING). This produced infinite retries
    on a non-self-healing condition, burning through the retry budget uselessly.
    """
    category = classify_failure(exc, stage=stage)
    return _CATEGORY_RETRYABLE.get(category, True)


def is_build_error(exc: Exception) -> bool:
    """
    [FIX-R4] Wrapper maintained for backward compatibility.

    Delegates to classify_failure() for structured classification.
    True if the exception is a deterministic compiler/linker error.
    """
    category = classify_failure(exc, stage="build")
    return category == FailureCategory.BUILD_ERROR


# ── RetryEngine ───────────────────────────────────────────────────────────────

class RetryEngine:
    """
    Stateful retry manager bound to a single session's execution run.

    One RetryEngine per Session. Tracks attempt history, computes backoff,
    and makes escalation decisions based on per-stage failure counts.

    The engine does NOT call asyncio.sleep itself unless you call .wait().
    The decision object tells you how long to sleep; you can substitute
    your own sleep strategy (e.g., a progress bar, a hardware reset sequence)
    in between.

    Usage::

        engine = RetryEngine(policy=RetryPolicy(max_attempts=3), session=session)

        while True:
            try:
                await run_build_stage()
                break
            except BuildError as exc:
                decision = engine.evaluate(exc, stage="build")

                if decision.outcome == RetryOutcome.RETRY:
                    await engine.wait(decision)
                    # loop continues from same stage

                elif decision.outcome == RetryOutcome.ESCALATE:
                    await engine.wait(decision)
                    engine.reset()
                    # jump back to planning stage

                else:  # ABORT
                    raise RetryExhaustedError(engine.attempt_count, exc) from exc
    """

    def __init__(
        self,
        policy:  Optional[RetryPolicy] = None,
        *,
        session: Optional["Session"]   = None,
    ) -> None:
        self._policy       = policy or RetryPolicy()
        self._session      = session
        self._decisions:   list[RetryDecision]     = []   # ALL decisions (full history)
        self._retry_count: int                     = 0    # [FIX-R2] only RETRY+ESCALATE
        self._stage_counts: dict[str, int]         = {}   # stage → consecutive failures
        self._last_failed_stage: Optional[str]      = None
        self._jitter_seed: str = (
            f"{self._policy.jitter_seed}:{session.id}" if session else self._policy.jitter_seed
        )
        self._total_wait_s: float                  = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def attempt_count(self) -> int:
        """
        [FIX-R2] Total number of RETRY or ESCALATE decisions made so far.

        Policy-based ABORT decisions (non-retryable category) are recorded
        in history but do NOT consume from the retry budget. This ensures
        that a single non-retryable error (e.g., syntax error in build) does
        not burn through the retry budget that was meant for transient failures.
        """
        return self._retry_count

    @property
    def attempts_remaining(self) -> int:
        return max(0, self._policy.max_attempts - self._retry_count)

    @property
    def is_exhausted(self) -> bool:
        return self._retry_count >= self._policy.max_attempts

    @property
    def total_wait_s(self) -> float:
        """Cumulative actual time spent in backoff across all retries."""
        return self._total_wait_s

    @property
    def history(self) -> list[RetryDecision]:
        """Full decision history including ABORTs — for auditability."""
        return list(self._decisions)

    def evaluate(
        self,
        exc: Exception,
        *,
        stage: str = "",
    ) -> RetryDecision:
        """
        Evaluate whether to RETRY, ESCALATE, or ABORT after an exception.

        Decision tree:
        1. Does the policy allow retry for `exc`?            → ABORT if not  [FIX-R1]
           (uses classify_failure for structured verdict)
        2. Is retry budget exhausted?                        → ABORT
        3. Has this stage failed enough times?               → ESCALATE (replan)
        4. Otherwise                                         → RETRY

        [FIX-R2] Non-retryable ABORTs (step 1) do NOT consume the retry budget.
        Only RETRY and ESCALATE decisions increment attempt_count.

        All decisions are recorded in the history log and in the bound session.
        """
        attempt  = self._retry_count   # [FIX-R2] use retry-only counter
        category = classify_failure(exc, stage=stage)   # [FIX-R1]

        # ── Guard: not retryable by policy ───────────────────────────────────
        # [FIX-R2] This ABORT does NOT consume a retry slot — it's an immediate
        # rejection based on the failure category, not budget exhaustion.
        if not self._policy.allows_retry(exc, stage=stage):
            d = self._make(
                outcome          = RetryOutcome.ABORT,
                attempt          = attempt,
                delay_s          = 0.0,
                reason           = (
                    f"{type(exc).__name__} (category={category.value}) "
                    f"is not retryable by policy"
                ),
                exception        = exc,
                stage            = stage,
                failure_category = category,
                consumes_budget  = False,   # [FIX-R2]
            )
            return d

        # ── Guard: budget exhausted ──────────────────────────────────────────
        if self.is_exhausted:
            d = self._make(
                outcome          = RetryOutcome.ABORT,
                attempt          = attempt,
                delay_s          = 0.0,
                reason           = f"Max attempts ({self._policy.max_attempts}) exhausted",
                exception        = exc,
                stage            = stage,
                failure_category = category,
                consumes_budget  = False,   # Already exhausted; no budget left
            )
            return d

        # ── Track per-stage failure count ────────────────────────────────────
        if stage and stage != self._last_failed_stage:
            self._stage_counts.clear()
        if stage:
            self._stage_counts[stage] = self._stage_counts.get(stage, 0) + 1
            self._last_failed_stage = stage
        consecutive_failures = self._stage_counts.get(stage, 0)

        # ── Escalate if the same stage keeps failing ─────────────────────────
        n = self._policy.escalate_after_n_stage_failures
        if (
            stage
            and stage != "plan"          # can't escalate from planning — already the top
            and consecutive_failures >= n
        ):
            rollback = "plan"
            d = self._make(
                outcome          = RetryOutcome.ESCALATE,
                attempt          = attempt,
                delay_s          = self._delay_for(attempt),
                reason           = (
                    f"Stage '{stage}' has failed {consecutive_failures} consecutive "
                    f"times (threshold {n}) — escalating to full replan"
                ),
                exception        = exc,
                stage            = stage,
                rollback_to      = rollback,
                failure_category = category,
                consumes_budget  = True,
            )
            return d

        # ── Normal retry ─────────────────────────────────────────────────────
        delay = self._delay_for(attempt)
        d = self._make(
            outcome          = RetryOutcome.RETRY,
            attempt          = attempt,
            delay_s          = delay,
            reason           = (
                f"Retryable error (category={category.value}) on attempt "
                f"{attempt + 1}/{self._policy.max_attempts} in stage '{stage}'"
            ),
            exception        = exc,
            stage            = stage,
            failure_category = category,
            consumes_budget  = True,
        )
        return d

    def _delay_for(self, attempt: int) -> float:
        """Deterministic per-session full-jitter backoff."""
        cap = self._policy.delay_cap_for(attempt)
        rng = random.Random(f"{self._jitter_seed}:{attempt}")
        return rng.uniform(0, cap)

    async def wait(self, decision: RetryDecision) -> None:
        """
        Sleep for the backoff duration specified in `decision`.

        This is the canonical place to log the wait, update a progress bar,
        or trigger a hardware reset sequence before the next attempt.

        [FIX-R3] _total_wait_s is updated AFTER the sleep completes (or in a
        finally clause that captures actual elapsed time). Previously, the full
        planned delay was added before sleeping, so if asyncio.CancelledError
        interrupted the sleep, the recorded telemetry overstated actual wait time.
        """
        if decision.delay_s <= 0:
            return

        if self._session:
            self._session.log(
                f"Retry backoff: {decision.delay_s:.2f}s "
                f"(attempt {decision.attempt + 1}/{self._policy.max_attempts}, "
                f"{decision.outcome.name}: {decision.reason})",
                delay_s          = decision.delay_s,
                outcome          = decision.outcome.name,
                failure_category = decision.failure_category.value if decision.failure_category else None,
            )

        # [FIX-R3] Track actual elapsed wait time, even on cancellation.
        t_start = asyncio.get_event_loop().time()
        try:
            await asyncio.sleep(decision.delay_s)
            self._total_wait_s += decision.delay_s
        except asyncio.CancelledError:
            # Record however long we actually waited before cancellation
            self._total_wait_s += asyncio.get_event_loop().time() - t_start
            raise

    def reset_stage_counts(self) -> None:
        """
        Reset per-stage consecutive failure counters.

        Called after an ESCALATE decision when the engine restarts from an
        earlier stage — the new plan means old failure counts are no longer
        meaningful.
        """
        self._stage_counts.clear()
        self._last_failed_stage = None

    def reset(self) -> None:
        """
        Full reset: clear attempt budget counters and stage counts.

        Use after an ESCALATE when you want the new planning + build cycle
        to have its own independent retry budget.

        Decision history is intentionally preserved for audit/replay safety.
        """
        self._stage_counts.clear()
        self._last_failed_stage = None
        self._retry_count = 0   # [FIX-R2]

    def stats(self) -> dict[str, Any]:
        """Return a structured summary for logging or telemetry."""
        return {
            "attempt_count":      self._retry_count,       # [FIX-R2]
            "attempts_remaining": self.attempts_remaining,
            "total_wait_s":       round(self._total_wait_s, 3),
            "stage_failures":     dict(self._stage_counts),
            "outcomes":           [d.outcome.name for d in self._decisions],
            "categories":         [
                d.failure_category.value if d.failure_category else "NONE"
                for d in self._decisions
            ],
        }

    # ── Private ───────────────────────────────────────────────────────────────

    def _make(
        self,
        *,
        outcome:          RetryOutcome,
        attempt:          int,
        delay_s:          float,
        reason:           str,
        exception:        Optional[Exception],
        stage:            str       = "",
        rollback_to:      Optional[str] = None,
        failure_category: Optional[FailureCategory] = None,
        consumes_budget:  bool = False,   # [FIX-R2]
    ) -> RetryDecision:
        d = RetryDecision(
            outcome          = outcome,
            attempt          = attempt,
            delay_s          = delay_s,
            reason           = reason,
            exception        = exception,
            rollback_to      = rollback_to,
            failure_category = failure_category,
        )
        self._decisions.append(d)

        # [FIX-R2] Only RETRY and ESCALATE consume from the retry budget.
        if consumes_budget and outcome in (RetryOutcome.RETRY, RetryOutcome.ESCALATE):
            self._retry_count += 1

        if self._session:
            self._session.log(
                f"RetryEngine: {outcome.name} — {reason}",
                attempt          = attempt,
                delay_s          = delay_s,
                stage            = stage,
                rollback_to      = rollback_to,
                failure_category = failure_category.value if failure_category else None,
            )

        return d
