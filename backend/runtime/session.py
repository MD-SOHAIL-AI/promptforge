"""
runtime/session.py — Session lifecycle management.

A Session is the single source of truth for one end-to-end execution run.
It owns the state machine, the context payload, and the full audit log.

Design contracts:
- Every state transition is validated against an explicit transition table.
  Illegal moves raise InvalidTransitionError immediately — no silent corruption.
- Terminal states (COMPLETED, FAILED, CANCELLED) are immutable. Once closed,
  the session is a read-only record.
- Observer callbacks are fire-and-forget. A crashing callback never propagates
  into the engine. The runtime must not be held hostage by UI code.
- The context object is mutable and intentionally shared (not copied) across
  pipeline stages. Each stage appends its artifact (plan, binary path, flash
  result, observations) in place. The engine reads the final state at the end.

State machine:

    CREATED
       │
       ▼
    PLANNING ──────────────────────────────────────────────────┐
       │                                                       │
       ▼                                                       │
    BUILDING ──────────── RETRYING ◄── (any stage failure)    │
       │                      │                               │
       ▼                      └──► PLANNING (escalation) ─────┘
    FLASHING                  └──► BUILDING (flash retry)
       │                      └──► FLASHING (observe retry)
       ▼                      └──► OBSERVING (rare)
    OBSERVING
       │
       ├──► COMPLETED   (terminal)
       ├──► FAILED      (terminal)
       └──► CANCELLED   (terminal) ← reachable from ANY non-terminal state

──────────────────────────────────────────────────────────────────────────────
HARDENING CHANGES vs. ORIGINAL
──────────────────────────────────────────────────────────────────────────────

[FIX-S1] CANCELLED state added as a proper terminal state.
  Risk: without it, asyncio.CancelledError leaves the session in a
  non-terminal, non-deterministic state. Replay tooling cannot tell
  "did this session finish?" from "was it abandoned mid-run?".

[FIX-S2] RETRYING → FLASHING and RETRYING → OBSERVING added to transition
  table. The original table was missing these, so retrying a failed flash
  or observe stage would raise InvalidTransitionError from inside the
  engine's retry loop, crashing the pipeline unconditionally.

[FIX-S3] All active states now accept CANCELLED as a target, so the engine's
  cancel() path can reach CANCELLED from any point in the pipeline without
  needing to know the current state.

[FIX-S4] _fire_callbacks() iterates over a copy of the callback list.
  An observer that adds/removes callbacks during notification previously
  caused silent list-mutation bugs on CPython (not a theoretical concern —
  IDE live-reload hooks do this).

[FIX-S5] _closed_mono is set on CANCELLED (same as COMPLETED/FAILED).
  elapsed_s was returning live monotonic time for cancelled sessions.

[FIX-S6] Event log hard cap (_MAX_EVENTS) with surgical trim that preserves
  all state_change and error events. A "truncated" marker is inserted so
  replay tooling can detect the gap. Prevents unbounded memory growth in
  long-running sessions with verbose build output.

[FIX-S7] state_history() now includes mono_ts and wall_ts per event.
  Without these, you cannot reconstruct how long each stage took from
  the history log alone — a forensic debugging regression.

[FIX-S8] cancel() convenience method on Session. The engine calls it;
  callers can also call it from signal handlers without knowing the
  internal state.
"""

from __future__ import annotations

import time
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


__all__ = [
    "SessionState",
    "SessionContext",
    "SessionEvent",
    "Session",
    "InvalidTransitionError",
    "StateChangeCallback",
]


# ── State machine definition ──────────────────────────────────────────────────

class SessionState(Enum):
    CREATED   = auto()   # Object instantiated; pipeline not yet started
    PLANNING  = auto()   # AI planner generating structured build plan
    BUILDING  = auto()   # Compiler / CMake / idf.py running
    FLASHING  = auto()   # Binary being written to target hardware
    OBSERVING = auto()   # Serial monitor open; collecting runtime output
    RETRYING  = auto()   # Backoff in progress before next attempt
    COMPLETED = auto()   # All stages succeeded    ← terminal
    FAILED    = auto()   # Unrecoverable error     ← terminal
    CANCELLED = auto()   # [FIX-S1] Externally cancelled ← terminal


# Every legal transition is explicitly listed.
# The engine must pass through this gate on every state move.
_VALID_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED:   frozenset({
        SessionState.PLANNING,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.PLANNING:  frozenset({
        SessionState.BUILDING,
        SessionState.FAILED,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.BUILDING:  frozenset({
        SessionState.FLASHING,
        SessionState.RETRYING,
        SessionState.FAILED,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.FLASHING:  frozenset({
        SessionState.OBSERVING,
        SessionState.RETRYING,
        SessionState.FAILED,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.OBSERVING: frozenset({
        SessionState.COMPLETED,
        SessionState.RETRYING,
        SessionState.FAILED,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.RETRYING:  frozenset({
        SessionState.PLANNING,
        SessionState.BUILDING,
        SessionState.FLASHING,                      # [FIX-S2] retry from flash stage
        SessionState.OBSERVING,                     # [FIX-S2] retry from observe stage
        SessionState.FAILED,
        SessionState.CANCELLED,                     # [FIX-S3]
    }),
    SessionState.COMPLETED: frozenset(),   # terminal — no exits
    SessionState.FAILED:    frozenset(),   # terminal — no exits
    SessionState.CANCELLED: frozenset(),   # terminal — no exits  [FIX-S1]
}

# All terminal states in one place — used for fast membership tests.
_TERMINAL_STATES: frozenset[SessionState] = frozenset({
    SessionState.COMPLETED,
    SessionState.FAILED,
    SessionState.CANCELLED,    # [FIX-S1]
})

# [FIX-S6] Hard cap on event log entries.
# At _MAX_EVENTS the log is trimmed: state_change and error events are always
# preserved; surplus log/metric events from the oldest portion are pruned.
# A synthetic "truncated" marker is inserted so replay tooling can detect gaps.
_MAX_EVENTS: int   = 100_000
_TRIM_TARGET: int  = 80_000    # After trimming, retain this many events


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class SessionEvent:
    """
    An immutable, timestamped record of something that happened during a session.

    Every state transition, log message, error, and metric produces one event.
    The full list forms an append-only audit log that can be replayed, exported
    to structured logging, or fed to an AI diagnostic agent.
    """
    kind:     str     # "state_change" | "log" | "error" | "metric" | "truncated"
    payload:  Any
    mono_ts:  float = field(default_factory=time.monotonic)  # For duration math
    wall_ts:  float = field(default_factory=time.time)        # For human timestamps


@dataclass
class SessionContext:
    """
    Mutable execution context threaded through all pipeline stages.

    Each stage reads what it needs and writes its output artifact back here.
    The engine reads the final state to construct the ExecutionResult.

    Fields are deliberately optional — a failed session may only have
    `prompt`, `target_id`, `working_dir`, and `error` populated.
    """

    # ── Inputs (set at session creation) ─────────────────────────────────────
    prompt:      str
    target_id:   str    # "esp32" | "stm32" | "arduino"
    working_dir: str

    # ── Stage outputs (populated as pipeline advances) ────────────────────────
    plan:           Optional[dict[str, Any]] = None   # Planner → Builder
    build_artifact: Optional[str]            = None   # Path to compiled .bin/.elf
    flash_result:   Optional[dict[str, Any]] = None   # Flash stage metadata
    observations:   list[str] = field(default_factory=list)  # Serial output lines
    error:          Optional[Exception]      = None   # Last recorded error

    # ── Freeform metadata ─────────────────────────────────────────────────────
    metadata:    dict[str, Any] = field(default_factory=dict)


StateChangeCallback = Callable[["Session", SessionState, SessionState], None]


# ── Exceptions ────────────────────────────────────────────────────────────────

class InvalidTransitionError(Exception):
    """Raised when code attempts an illegal session state transition."""

    def __init__(
        self,
        from_state: SessionState,
        to_state: SessionState,
    ) -> None:
        allowed = sorted(
            s.name
            for s in _VALID_TRANSITIONS.get(from_state, frozenset())
        )
        super().__init__(
            f"Invalid session transition: {from_state.name} → {to_state.name}. "
            f"Allowed from {from_state.name}: [{', '.join(allowed) or 'none — terminal state'}]"
        )
        self.from_state = from_state
        self.to_state   = to_state


# ── Session ───────────────────────────────────────────────────────────────────

class Session:
    """
    Lifecycle manager and audit log for one PromptForge execution run.

    Thread-safety note: Session is designed for use within a single asyncio
    event loop. It is not thread-safe across OS threads. Do not share a Session
    between threads — create one Session per execution and keep it on the same
    loop that created it.

    Cancellation: call session.cancel(reason) or engine.cancel(). The session
    transitions to CANCELLED (a terminal state), and the engine's CancelledError
    handler calls cancel() automatically if a task is externally cancelled.

    Example::

        ctx = SessionContext(
            prompt      = "Blink the built-in LED at 2 Hz",
            target_id   = "esp32",
            working_dir = "/workspace/runs/run-001",
        )
        session = Session(context=ctx)

        session.transition(SessionState.PLANNING)
        ctx.plan = await planner.plan(...)

        session.transition(SessionState.BUILDING)
        ctx.build_artifact = await builder.build(...)

        session.transition(SessionState.COMPLETED)
        print(session.summary())
    """

    def __init__(
        self,
        context: SessionContext,
        *,
        session_id: Optional[str]                  = None,
        on_state_change: Optional[StateChangeCallback] = None,
    ) -> None:
        self._id        = session_id or str(uuid.uuid4())
        self._context   = context
        self._state     = SessionState.CREATED
        self._events:   list[SessionEvent]         = []
        self._callbacks: list[StateChangeCallback] = []
        self._created_wall  = time.time()
        self._created_mono  = time.monotonic()
        self._closed_mono: Optional[float]         = None
        self._event_truncations: int               = 0   # [FIX-S6]
        self._lock = threading.RLock()

        if on_state_change:
            self._callbacks.append(on_state_change)

        self._append_event("state_change", {
            "from":    None,
            "to":      SessionState.CREATED.name,
            "wall_ts": self._created_wall,
            "mono_ts": self._created_mono,
        })

    # ── Identity / inspection ─────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return self._id

    @property
    def state(self) -> SessionState:
        with self._lock:
            return self._state

    @property
    def context(self) -> SessionContext:
        return self._context

    @property
    def is_terminal(self) -> bool:
        """True once the session has reached COMPLETED, FAILED, or CANCELLED."""
        with self._lock:
            return self._state in _TERMINAL_STATES   # [FIX-S1] includes CANCELLED

    @property
    def elapsed_s(self) -> float:
        """Wall-clock seconds since session creation."""
        with self._lock:
            end = self._closed_mono or time.monotonic()
            return end - self._created_mono

    @property
    def events(self) -> list[SessionEvent]:
        """Defensive copy of the immutable event log."""
        with self._lock:
            return list(self._events)

    # ── State machine ─────────────────────────────────────────────────────────

    def transition(self, to_state: SessionState) -> None:
        """
        Advance the session to `to_state`.

        Raises:
            InvalidTransitionError: if the move is not in the transition table.
            InvalidTransitionError: if the session is already in a terminal state.
        """
        with self._lock:
            if self._state in _TERMINAL_STATES:
                raise InvalidTransitionError(self._state, to_state)

            allowed = _VALID_TRANSITIONS.get(self._state, frozenset())
            if to_state not in allowed:
                raise InvalidTransitionError(self._state, to_state)

            from_state   = self._state
            self._state  = to_state

            # [FIX-S5] Set _closed_mono for ALL terminal states, including CANCELLED.
            # Previously only COMPLETED and FAILED were covered, so elapsed_s kept
            # ticking for cancelled sessions.
            if to_state in _TERMINAL_STATES:
                self._closed_mono = time.monotonic()

            now_mono = time.monotonic()
            now_wall = time.time()
            # [FIX-S7] Include timestamps directly in the state_change payload so
            # state_history() can reconstruct per-stage timing without scanning events.
            self._append_event("state_change", {
                "from":    from_state.name,
                "to":      to_state.name,
                "mono_ts": now_mono,
                "wall_ts": now_wall,
            })

        # Callbacks run outside the mutation lock so UI/observer code cannot
        # deadlock the state machine or extend the critical section.
        self._fire_callbacks(from_state, to_state)

    def cancel(self, reason: str = "external cancellation") -> None:
        """
        [FIX-S8] Transition the session to CANCELLED.

        Safe to call from any non-terminal state. No-op if already terminal —
        the session may have completed between the cancel signal and this call.

        This is the canonical way to cancel a session from outside the engine
        (e.g., SIGINT handler, timeout watchdog, user abort request).

        The engine's execute() also calls this automatically when it catches
        asyncio.CancelledError.
        """
        with self._lock:
            if self._state in _TERMINAL_STATES:
                return
            if not self._context.error:
                self._context.error = RuntimeError(f"Session cancelled: {reason}")
            self._append_event("log", {
                "message": f"Session cancellation requested: {reason}",
            })
        self.transition(SessionState.CANCELLED)

    def add_observer(self, callback: StateChangeCallback) -> None:
        """Register an additional state-change observer."""
        with self._lock:
            self._callbacks.append(callback)

    # ── Logging / instrumentation ─────────────────────────────────────────────

    def log(self, message: str, **kwargs: Any) -> None:
        """
        Append a freeform log entry to the event log.

        All keyword arguments are stored as structured payload fields,
        making the log machine-readable for downstream AI diagnostic agents.

        Example::
            session.log("Build started", cwd=ctx.working_dir, target=ctx.target_id)
        """
        with self._lock:
            self._append_event("log", {"message": message, **kwargs})

    def record_error(self, exc: Exception, *, stage: str = "") -> None:
        """
        Record an exception in both the event log and the context.

        Does NOT transition the state — that remains the engine's responsibility.
        Records the most recent error; earlier errors remain in the event log.
        """
        with self._lock:
            self._context.error = exc
            self._append_event("error", {
                "type":    type(exc).__name__,
                "message": str(exc),
                "stage":   stage,
            })

    def record_metric(
        self,
        name: str,
        value: float,
        unit: str = "",
        *,
        stage: str = "",
    ) -> None:
        """
        Record a numeric metric.

        Typical usage: build_duration_s, flash_speed_kbps, observe_line_count.
        These metrics are visible in session.summary() and can be charted
        across runs to track regression.
        """
        with self._lock:
            self._append_event("metric", {
                "name":  name,
                "value": value,
                "unit":  unit,
                "stage": stage,
            })

    # ── Serialization ─────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """
        JSON-serializable snapshot of the session.

        Includes aggregated metrics from the event log so the caller does
        not have to scan events manually.
        """
        with self._lock:
            metrics = {
                ev.payload["name"]: {
                    "value": ev.payload["value"],
                    "unit":  ev.payload["unit"],
                }
                for ev in self._events
                if ev.kind == "metric"
            }

            error_events = [
                ev.payload
                for ev in self._events
                if ev.kind == "error"
            ]

            return {
                "id":                self._id,
                "state":             self._state.name,
                "target_id":         self._context.target_id,
                "prompt":            self._context.prompt[:200],
                "elapsed_s":         round(self.elapsed_s, 3),
                "created_at":        self._created_wall,
                "event_count":       len(self._events),
                "event_truncations": self._event_truncations,   # [FIX-S6]
                "metrics":           metrics,
                "errors":            error_events,
                "observations":      self._context.observations[-50:],  # last 50 lines
                "observation_total": len(self._context.observations),   # full count
            }

    def state_history(self) -> list[dict[str, Any]]:
        """
        Return all state-change events in chronological order.

        [FIX-S7] Now includes mono_ts and wall_ts per event so callers can
        compute per-stage duration without cross-referencing raw events.
        """
        with self._lock:
            return [
                {
                    "from":    ev.payload.get("from"),
                    "to":      ev.payload.get("to"),
                    "mono_ts": ev.payload.get("mono_ts", ev.mono_ts),
                    "wall_ts": ev.payload.get("wall_ts", ev.wall_ts),
                }
                for ev in self._events
                if ev.kind == "state_change"
            ]

    def __repr__(self) -> str:
        return (
            f"<Session id={self._id[:8]} state={self._state.name} "
            f"target={self._context.target_id} elapsed={self.elapsed_s:.1f}s>"
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _append_event(self, kind: str, payload: Any) -> None:
        self._events.append(SessionEvent(kind=kind, payload=payload))

        # [FIX-S6] Safety valve: trim the event log when it grows too large.
        # Check only when exactly at the cap to avoid O(n) on every append.
        if len(self._events) >= _MAX_EVENTS:
            self._trim_event_log()

    def _trim_event_log(self) -> None:
        """
        [FIX-S6] Trim surplus log/metric events while preserving all
        state_change and error events (essential for replay safety).

        A synthetic 'truncated' marker is inserted at the trim point so
        replay tooling and forensic debuggers can detect the gap.
        """
        essential_kinds = {"state_change", "error", "truncated"}
        to_drop = len(self._events) - _TRIM_TARGET
        if to_drop <= 0:
            return

        kept: list[SessionEvent] = []
        dropped = 0
        marker_inserted = False
        marker: Optional[SessionEvent] = None

        for event in self._events:
            if dropped < to_drop and event.kind not in essential_kinds:
                dropped += 1
                if marker is None:
                    marker = SessionEvent(
                        kind    = "truncated",
                        payload = {
                            "dropped":      0,
                            "message":      "Event log trimmed; non-essential events dropped",
                            "replay_safe":  False,
                        },
                        mono_ts = event.mono_ts,
                        wall_ts = event.wall_ts,
                    )
                continue
            if marker is not None and not marker_inserted:
                marker.payload["dropped"] = dropped
                kept.append(marker)
                marker_inserted = True
            kept.append(event)

        if marker is not None and not marker_inserted:
            marker.payload["dropped"] = dropped
            kept.append(marker)

        self._events = kept
        self._event_truncations += 1

    def _fire_callbacks(
        self,
        from_state: SessionState,
        to_state: SessionState,
    ) -> None:
        """
        Notify all registered observers of the state change.

        Exceptions in callbacks are caught and logged — a crashing observer
        must never abort a pipeline that is otherwise succeeding.

        [FIX-S4] Iterates over a snapshot copy of _callbacks. An observer that
        adds or removes callbacks during notification would otherwise cause a
        list-mutation bug (or silently skip/double-fire in some implementations).
        """
        with self._lock:
            callbacks = list(self._callbacks)

        for cb in callbacks:   # [FIX-S4] iterate a copy
            try:
                cb(self, from_state, to_state)
            except Exception as exc:
                # Record the callback failure without crashing the runtime
                with self._lock:
                    self._append_event("error", {
                        "type":    "CallbackError",
                        "message": f"Observer {cb!r} raised: {exc}",
                        "stage":   "callback",
                    })
