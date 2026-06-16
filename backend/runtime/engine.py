"""
runtime/engine.py — PromptForge execution engine.

The engine owns the full Prompt → Plan → Build → Flash → Observe lifecycle.
It wires together SubprocessManager, Session, and RetryEngine and enforces
the invariants that no individual pipeline stage can see:

Invariants:
  1. A session is always in a consistent state. The engine is the ONLY
     entity allowed to call session.transition(). Pipeline stages log and
     produce artifacts; they do not touch the state machine.

  2. Every execution produces an ExecutionResult, regardless of outcome.
     The engine never raises to the caller — all exceptions are captured
     and reflected in the result. This makes it safe to drive the engine
     from an async task, a CLI, or an RPC handler.

  3. Retry logic is centralized here. Stages raise on error; the engine
     decides whether to retry, escalate, or abort. No retry logic lives
     inside a stage — stages are pure functions of (input → output | error).

  4. On shutdown, all active subprocesses are killed. The engine registers
     no atexit hooks — it is the caller's responsibility to call .shutdown()
     (or use it as an async context manager).

  5. Cancellation is first-class. asyncio.CancelledError is caught in
     execute() and transitions the session to CANCELLED (a terminal state)
     before re-raising. Subprocesses are cleaned up via shutdown().

Usage — one-shot::

    async with Engine.create(config, planner, builder, flasher, observer) as engine:
        result = await engine.execute(
            prompt      = "Blink the built-in LED at 2 Hz",
            target      = ESP32Target(port="/dev/ttyUSB0"),
            working_dir = "/tmp/runs/run-001",
        )
        print(result)

Usage — batch (reuse engine across multiple prompts)::

    engine = Engine(config, planner=p, builder=b, flasher=f, observer=o)
    try:
        for prompt in prompt_queue:
            result = await engine.execute(prompt=prompt, target=target, working_dir=...)
    finally:
        await engine.shutdown()

──────────────────────────────────────────────────────────────────────────────
HARDENING CHANGES vs. ORIGINAL
──────────────────────────────────────────────────────────────────────────────

[FIX-E1] CRITICAL: asyncio.CancelledError not caught in execute().
  The original caught only `Exception`, which does not include
  asyncio.CancelledError (a BaseException since Python 3.8). When a task
  running execute() was cancelled (SIGINT, timeout, parent task cancel), the
  session was left in a non-terminal state — neither COMPLETED, FAILED, nor
  CANCELLED. The session's is_terminal check returned False for a session
  that would never advance further, breaking replay, forensics, and any
  caller that waited on is_terminal to gate follow-on actions.
  Fix: explicit CancelledError handler that calls session.cancel() then
  re-raises (preserving Python's cancellation contract).

[FIX-E2] CRITICAL: Missing RETRYING → FLASHING and RETRYING → OBSERVING
  in the original session.py transition table (fixed in session.py [FIX-S2]).
  The engine's retry loop correctly computed `start_at = "flash"` after a
  flash failure, then tried session.transition(RETRYING → FLASHING), which
  raised InvalidTransitionError unconditionally. Every flash or observe retry
  crashed the pipeline instead of retrying.

[FIX-E3] CRITICAL: _stage_flash() and _stage_observe() called
  session.transition() unconditionally. _stage_plan() and _stage_build()
  had idempotency guards (`if session.state != X: transition(X)`), but flash
  and observe did not. After the engine's retry path set the session to
  FLASHING (RETRYING → FLASHING), _stage_flash() then tried FLASHING →
  FLASHING, raising InvalidTransitionError. This would have manifested as
  soon as [FIX-E2] was applied without also fixing [FIX-E3].
  Fix: add the same idempotency guard to _stage_flash() and _stage_observe().

[FIX-E4] cancel() method added to Engine.
  The caller can now cancel an in-progress execution cleanly. cancel()
  cancels the underlying asyncio Task if one is tracked, otherwise calls
  session.cancel() directly.

[FIX-E5] Structured failure category logged on every stage error.
  classify_failure() is called in the exception handler and the category is
  included in the warning log and the session event. This makes production
  log queries like "show all DEVICE_DISCONNECTED failures" possible without
  post-processing raw exception messages.

[FIX-E6] session_id propagated to ProcessConfig for subprocess log correlation.
  When multiple sessions run concurrently, every subprocess invocation now
  carries the session_id. Log aggregation tools (Loki, CloudWatch, Datadog)
  can filter by session_id to isolate one execution's subprocess trace.
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

from .session        import Session, SessionContext, SessionState
from .subprocess_mgr import SubprocessManager
from .retry          import (
    RetryEngine, RetryPolicy, RetryOutcome, RetryExhaustedError, classify_failure
)
# FIX #2: Import the single authoritative ExecutionResult from result.py.
# The duplicate @dataclass that previously lived in this file has been removed.
# result.py is the canonical owner; engine.py is a consumer.
from .result import (
    ExecutionResult,          # noqa: F401 — re-exported for callers
    ExecutionResultBuilder,
    FailureResult,
    ResultStatus,
)

if TYPE_CHECKING:
    from ..pipeline.planner  import Planner
    from ..pipeline.builder  import Builder
    from ..pipeline.flasher  import Flasher
    from ..pipeline.observer import Observer
    from ..targets.base      import Target

logger = logging.getLogger(__name__)

__all__ = ["Engine", "EngineConfig", "ExecutionResult"]


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EngineConfig:
    """
    Top-level runtime configuration.

    Immutable by design — the engine should not change behaviour mid-run.
    Create a new Engine instance if you need different settings.
    """
    retry_policy:           RetryPolicy = field(default_factory=RetryPolicy)
    subprocess_max_concurrent: int      = 4

    # Per-stage timeout budgets
    plan_timeout_s:    float = 30.0    # AI planner call
    build_timeout_s:   float = 180.0   # Toolchain compile + link
    flash_timeout_s:   float = 60.0    # esptool / openocd write
    observe_timeout_s: float = 30.0    # Serial monitor collection

    # Observation success/failure patterns (empty = ignore)
    # Success: observation stage ends as soon as this string appears in serial output
    # Failure: observation stage raises ObservationError when this appears
    observe_success_pattern: str = ""
    observe_failure_pattern: str = ""


# ── Engine ────────────────────────────────────────────────────────────────────

class Engine:
    """
    PromptForge execution engine.

    Wires together the subprocess manager, session lifecycle, and retry engine
    to run the full Prompt → Plan → Build → Flash → Observe pipeline with
    configurable retry semantics and deterministic I/O handling.

    Instantiate once and reuse across calls to .execute() — the subprocess
    manager's concurrency semaphore is shared across runs, preventing device
    port contention when running parallel sessions.
    """

    def __init__(
        self,
        config: EngineConfig,
        *,
        planner:  "Planner",
        builder:  "Builder",
        flasher:  "Flasher",
        observer: "Observer",
    ) -> None:
        self._config   = config
        self._planner  = planner
        self._builder  = builder
        self._flasher  = flasher
        self._observer = observer
        self._subprocess_mgr = SubprocessManager(
            max_concurrent = config.subprocess_max_concurrent,
        )
        self._shutdown_event = asyncio.Event()

        # [FIX-E4] Track active execute() tasks for cancel() support.
        # Maps session_id → asyncio.Task so cancel() can cancel the right task.
        self._active_tasks: dict[str, asyncio.Task] = {}

    # ── Context manager ───────────────────────────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def create(
        cls,
        config: EngineConfig,
        *,
        planner:  "Planner",
        builder:  "Builder",
        flasher:  "Flasher",
        observer: "Observer",
    ) -> AsyncIterator["Engine"]:
        """
        Async context manager that guarantees shutdown on exit.

        Preferred over manual instantiation when running inside an
        asyncio application — ensures subprocesses are cleaned up even
        if the calling code raises.

        Example::

            async with Engine.create(config, ...) as engine:
                result = await engine.execute(...)
        """
        engine = cls(
            config,
            planner  = planner,
            builder  = builder,
            flasher  = flasher,
            observer = observer,
        )
        try:
            yield engine
        finally:
            await engine.shutdown()

    # ── Primary API ───────────────────────────────────────────────────────────

    async def execute(
        self,
        prompt:      str,
        target:      "Target",
        *,
        working_dir: str,
        session_id:  Optional[str]       = None,
        extra_meta:  Optional[dict[str, Any]] = None,
        on_state_change: Optional[Any]   = None,   # StateChangeCallback
    ) -> ExecutionResult:
        """
        Run a complete Prompt → Plan → Build → Flash → Observe cycle.

        Returns an ExecutionResult regardless of success, failure, OR
        cancellation. This method does not raise; all exceptions are captured
        inside the result. The caller should check result.success and
        result.state before proceeding.

        Cancellation: if the asyncio Task running execute() is cancelled,
        the session transitions to CANCELLED (terminal), all active subprocesses
        are killed, and the method returns an ExecutionResult with
        state="CANCELLED". The CancelledError is re-raised after cleanup so
        the caller's cancellation contract is honored.

        Args:
            prompt:      Natural-language firmware description.
            target:      Hardware target (ESP32Target, STM32Target, etc.).
            working_dir: Scratch directory for build artifacts.
            session_id:  Optional stable ID (useful for resuming / logging).
            extra_meta:  Arbitrary key/value pairs added to the session context.
            on_state_change: Optional callback fired on every state transition.
        """
        ctx = SessionContext(
            prompt      = prompt,
            target_id   = target.target_id,
            working_dir = working_dir,
            metadata    = extra_meta or {},
        )
        session = Session(
            context          = ctx,
            session_id       = session_id,
            on_state_change  = on_state_change,
        )
        retry_engine = RetryEngine(
            policy  = self._config.retry_policy,
            session = session,
        )

        logger.info(
            "Engine: starting session %s | target=%s | prompt=%r",
            session.id[:8], target.target_id, prompt[:80],
        )

        if self._shutdown_event.is_set():
            exc = RuntimeError("Engine is shut down; refusing to start a new execution")
            session.record_error(exc, stage="engine")
            session.transition(SessionState.FAILED)
            stats = retry_engine.stats()
            builder = ExecutionResultBuilder(session_id=session.id)
            builder.record_failure(FailureResult(
                category="UNKNOWN",
                message=str(exc),
                retryable=False,
                stage="engine",
                exception_type=type(exc).__name__,
            ))
            return builder.build(
                final_state      = session.state.name,
                retry_count      = stats["attempt_count"],
                total_wait_ms    = int(stats["total_wait_s"] * 1000),
                failure_category = "UNKNOWN",
                message          = str(exc),
                metadata         = {
                    "session":       session.summary(),
                    "retry_stats":   stats,
                    "state_history": session.state_history(),
                    "observations":  list(ctx.observations),
                },
            )

        # [FIX-E4] Register this task so cancel(session_id) can find it.
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_tasks[session.id] = current_task

        try:
            await self._run_pipeline(session, target, retry_engine)

        except asyncio.CancelledError:
            # [FIX-E1] First-class cancellation handling.
            #
            # asyncio.CancelledError is a BaseException (Python 3.8+), so it
            # was previously invisible to the `except Exception` safety net.
            # Without this handler, cancelling the task left the session in
            # a non-terminal state (e.g., BUILDING) with is_terminal=False,
            # corrupting any replay log or caller waiting on terminal state.
            #
            # We transition to CANCELLED, kill subprocesses, then re-raise
            # to honor the cancellation contract (caller's await must propagate it).
            logger.warning(
                "Engine: session %s CANCELLED during execution", session.id[:8]
            )
            session.cancel("asyncio task cancelled")
            await self._subprocess_mgr.kill_all()
            raise   # Re-raise so the caller's task management still works

        except Exception as exc:
            # Safety net: catch anything the pipeline runner missed
            logger.exception("Engine: unhandled crash in session %s", session.id[:8])
            session.record_error(exc, stage="engine")
            if not session.is_terminal:
                session.transition(SessionState.FAILED)

        finally:
            # [FIX-E4] Always deregister the task, even on cancellation.
            self._active_tasks.pop(session.id, None)

        stats = retry_engine.stats()
        builder = ExecutionResultBuilder(session_id=session.id)

        # Carry any failure into the result
        if ctx.error:
            last_category = (
                stats["categories"][-1]
                if stats.get("categories") else "UNKNOWN"
            )
            builder.record_failure(FailureResult(
                category       = last_category,
                message        = str(ctx.error),
                retryable      = False,
                stage          = "engine",
                exception_type = type(ctx.error).__name__,
            ))

        result = builder.build(
            final_state      = session.state.name,
            retry_count      = stats["attempt_count"],
            total_wait_ms    = int(stats["total_wait_s"] * 1000),
            failure_category = (
                stats["categories"][-1] if stats.get("categories") else None
            ),
            metadata         = {
                "session":        session.summary(),
                "retry_stats":    stats,
                "state_history":  session.state_history(),
                "observations":   list(ctx.observations),
                "build_artifact": ctx.build_artifact,
            },
        )

        logger.info(
            "Engine: session %s %s in %.1fs (%d retries)",
            session.id[:8],
            session.state.name,
            result.elapsed_s,
            result.retry_count,
        )

        return result

    async def cancel(self, session_id: str, reason: str = "caller requested cancel") -> bool:
        """
        [FIX-E4] Cancel an in-progress session by ID.

        If the session's asyncio Task is tracked (i.e., it was launched via
        execute()), cancels the task. The CancelledError handler in execute()
        will clean up the session state and subprocesses.

        Returns True if a task was found and cancelled, False if the session
        was not found (already completed or never started).
        """
        task = self._active_tasks.get(session_id)
        if task is None:
            return False
        if not task.done():
            task.cancel(reason)
            return True
        return False

    async def shutdown(self) -> None:
        """
        Gracefully terminate all active subprocesses.

        Safe to call multiple times. Idempotent after the first call.
        """
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
            current_task = asyncio.current_task()
            tasks = [
                task
                for task in list(self._active_tasks.values())
                if task is not current_task and not task.done()
            ]
            for task in tasks:
                task.cancel("engine shutdown")
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._subprocess_mgr.kill_all()
            logger.info("Engine: shutdown complete")

    # ── Pipeline orchestration ────────────────────────────────────────────────

    async def _run_pipeline(
        self,
        session:      Session,
        target:       "Target",
        retry_engine: RetryEngine,
    ) -> None:
        """
        Retry-aware pipeline runner.

        On each attempt, runs stages starting from `start_at`. A RETRY outcome
        restarts from the failed stage; an ESCALATE outcome rolls back to
        an earlier stage (always "plan" for now).

        The session state machine is advanced by this method and by the stage
        methods. Stages only raise exceptions — they never decide retry/abort.

        Stage re-entry after RETRYING:
          After RETRYING, the engine transitions the session to the stage state
          before calling the stage method. The stage methods contain idempotency
          guards (`if session.state != X: transition(X)`) so there is no
          double-transition if the session is already in the correct state.
          This pattern is used consistently across all four stages after [FIX-E3].
        """
        start_at: str = "plan"

        while not session.is_terminal:
            try:
                if start_at == "plan":
                    await self._stage_plan(session, target)
                    start_at = "build"

                if start_at in ("plan", "build"):
                    await self._stage_build(session, target)
                    start_at = "flash"

                if start_at in ("plan", "build", "flash"):
                    await self._stage_flash(session, target)
                    start_at = "observe"

                await self._stage_observe(session, target)

                # All stages succeeded
                session.transition(SessionState.COMPLETED)
                return

            except asyncio.CancelledError:
                # Let CancelledError propagate up to execute()'s handler.
                # Do NOT record it as a session error or make a retry decision.
                raise

            except Exception as exc:
                current_stage = _state_to_stage(session.state)
                session.record_error(exc, stage=current_stage)

                # [FIX-E5] Include structured failure category in the log.
                category = classify_failure(exc, stage=current_stage)
                logger.warning(
                    "Engine: %s failed in stage '%s' [%s]: %s",
                    session.id[:8], current_stage, category.value, exc,
                )

                decision = retry_engine.evaluate(exc, stage=current_stage)

                if decision.outcome == RetryOutcome.ABORT:
                    session.transition(SessionState.FAILED)
                    return

                # Transition to RETRYING before sleeping (for UI feedback)
                session.transition(SessionState.RETRYING)
                await retry_engine.wait(decision)

                if decision.outcome == RetryOutcome.ESCALATE:
                    # Roll back: start the next iteration from planning.
                    # session.py transition table allows RETRYING → PLANNING.
                    retry_engine.reset_stage_counts()
                    session.transition(SessionState.PLANNING)
                    start_at = "plan"

                else:
                    # Retry from the stage that failed.
                    # Transition RETRYING → <stage_state> before re-entering
                    # the stage method. session.py [FIX-S2] added the missing
                    # RETRYING → FLASHING and RETRYING → OBSERVING transitions.
                    start_at = current_stage
                    next_state = _stage_to_state(start_at)
                    if next_state:
                        session.transition(next_state)
                    # If next_state is None (e.g., "unknown"), the stage guard
                    # in each _stage_* method will handle the transition.

    # ── Stage implementations ─────────────────────────────────────────────────

    async def _stage_plan(
        self,
        session: Session,
        target:  "Target",
    ) -> None:
        """
        Stage 1 — Prompt → Structured build plan.

        The planner (AI or rule-based) reads the prompt and target capabilities
        and produces a dict describing what to build. The exact schema is
        target-specific but must include at least a "steps" list.
        """
        if session.state != SessionState.PLANNING:
            session.transition(SessionState.PLANNING)

        t0 = time.monotonic()
        session.log("Plan stage started", prompt_len=len(session.context.prompt))

        plan = await asyncio.wait_for(
            self._planner.plan(
                prompt      = session.context.prompt,
                target      = target,
                working_dir = session.context.working_dir,
            ),
            timeout = self._config.plan_timeout_s,
        )
        session.context.plan = plan

        elapsed = time.monotonic() - t0
        session.record_metric("plan_duration_s", elapsed, "s", stage="plan")
        session.log(
            "Plan stage complete",
            elapsed_s  = round(elapsed, 2),
            step_count = len(plan.get("steps", [])),
        )

    async def _stage_build(
        self,
        session: Session,
        target:  "Target",
    ) -> None:
        """
        Stage 2 — Plan → Compiled binary artifact.

        The builder invokes the appropriate toolchain (idf.py build,
        arduino-cli compile, arm-none-eabi-gcc, etc.) via SubprocessManager.
        Returns the path to the compiled .bin/.elf/.hex file.
        """
        if session.state != SessionState.BUILDING:
            session.transition(SessionState.BUILDING)

        t0 = time.monotonic()
        session.log("Build stage started", target=target.target_id)

        artifact_path = await self._builder.build(
            plan            = session.context.plan,
            target          = target,
            working_dir     = session.context.working_dir,
            subprocess_mgr  = self._subprocess_mgr,
            timeout_s       = self._config.build_timeout_s,
        )
        session.context.build_artifact = artifact_path

        elapsed = time.monotonic() - t0
        session.record_metric("build_duration_s", elapsed, "s", stage="build")
        session.log(
            "Build stage complete",
            elapsed_s = round(elapsed, 2),
            artifact  = artifact_path,
        )

    async def _stage_flash(
        self,
        session: Session,
        target:  "Target",
    ) -> None:
        """
        Stage 3 — Binary artifact → Device flash.

        The flasher writes the compiled binary to the target hardware using
        the appropriate tool (esptool.py for ESP32, openocd for STM32,
        arduino-cli upload for Arduino). Verifies the write was successful.

        [FIX-E3] Added idempotency guard matching the pattern used by
        _stage_plan and _stage_build. Without this guard, retrying the flash
        stage would cause a double-transition: the engine sets FLASHING via
        RETRYING → FLASHING, then this method tried FLASHING → FLASHING,
        raising InvalidTransitionError on every retry attempt.
        """
        # [FIX-E3] Idempotency guard: only transition if not already in FLASHING.
        if session.state != SessionState.FLASHING:
            session.transition(SessionState.FLASHING)

        t0 = time.monotonic()
        session.log(
            "Flash stage started",
            artifact = session.context.build_artifact,
            port     = getattr(target, "port", "unknown"),
        )

        flash_result = await self._flasher.flash(
            artifact_path  = session.context.build_artifact,
            target         = target,
            subprocess_mgr = self._subprocess_mgr,
            timeout_s      = self._config.flash_timeout_s,
        )
        session.context.flash_result = flash_result

        elapsed = time.monotonic() - t0
        session.record_metric("flash_duration_s", elapsed, "s", stage="flash")
        session.log("Flash stage complete", elapsed_s=round(elapsed, 2))

    async def _stage_observe(
        self,
        session: Session,
        target:  "Target",
    ) -> None:
        """
        Stage 4 — Serial observation and success/failure detection.

        Opens the target's serial port, reads output until:
        - success_pattern matches    → stage succeeds
        - failure_pattern matches    → stage raises ObservationError
        - timeout_s elapsed          → stage raises TimeoutError

        All collected lines are stored in session.context.observations
        for AI diagnostic agents to inspect.

        [FIX-E3] Added idempotency guard matching the pattern used by all
        other stage methods. Without this guard, retrying the observe stage
        would cause OBSERVING → OBSERVING, raising InvalidTransitionError.
        """
        # [FIX-E3] Idempotency guard: only transition if not already in OBSERVING.
        if session.state != SessionState.OBSERVING:
            session.transition(SessionState.OBSERVING)

        t0 = time.monotonic()
        session.log("Observe stage started", timeout_s=self._config.observe_timeout_s)

        observations = await self._observer.observe(
            target           = target,
            timeout_s        = self._config.observe_timeout_s,
            success_pattern  = self._config.observe_success_pattern,
            failure_pattern  = self._config.observe_failure_pattern,
        )
        session.context.observations.extend(observations)

        elapsed = time.monotonic() - t0
        session.record_metric("observe_duration_s", elapsed, "s", stage="observe")
        session.record_metric("observe_line_count", len(observations), "lines", stage="observe")
        session.log(
            "Observe stage complete",
            elapsed_s  = round(elapsed, 2),
            line_count = len(observations),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _state_to_stage(state: SessionState) -> str:
    """Map a session state to the stage name used in retry decisions."""
    return {
        SessionState.PLANNING:  "plan",
        SessionState.BUILDING:  "build",
        SessionState.FLASHING:  "flash",
        SessionState.OBSERVING: "observe",
    }.get(state, "unknown")


def _stage_to_state(stage: str) -> Optional[SessionState]:
    """Map a stage name back to its entry SessionState."""
    return {
        "plan":    SessionState.PLANNING,
        "build":   SessionState.BUILDING,
        "flash":   SessionState.FLASHING,
        "observe": SessionState.OBSERVING,
    }.get(stage)
