"""
runtime/result.py — Shared result contracts for the PromptForge AI runtime.

PURPOSE
=======
This module is the common language spoken by all runtime components.
Every runtime operation returns a structured, typed result rather than a raw
dictionary, tuple, boolean, or unstructured string.

The result layer defines contracts ONLY. It does not:
  - own orchestration
  - own retries
  - own classification
  - own state transitions

HIERARCHY
=========

    Result (base)
    ├── ProcessResult      — subprocess_mgr.py output (re-exported + hierarchy member)
    ├── BuildResult        — build_firmware.py output
    ├── FlashResult        — flash_firmware.py output
    ├── ObserveResult      — serial_runtime.py / observer output
    ├── SimulationResult   — wokwi_simulator.py output
    └── ExecutionResult    — engine.py top-level output

    FailureResult          — companion object, composable into any Result on failure
    ExecutionResultBuilder — mutable builder; produces immutable ExecutionResult

DESIGN INVARIANTS
=================
  - All result objects are immutable (frozen dataclasses) once constructed.
  - All fields are serialisable to JSON without custom encoders.
    Enums → .value (str). Optional fields → None. Timestamps → float (epoch).
    duration_ms → int (avoids float precision loss in round-trips).
  - No result object imports session.py, retry.py, engine.py, or any
    pipeline stage. The dependency arrow points inward only.
  - ProcessResult is not redefined here; it is imported from subprocess_mgr
    and re-exported so callers have a single import surface.
  - Partial-success states are first-class. ExecutionResult fields for stages
    that were never reached are None — not empty strings, not sentinel objects.

REPLAY SAFETY
=============
  All result objects are designed to survive serialise → deserialise → compare
  round-trips identically. This is required for:
    - Test replay and golden-file comparison
    - Audit log persistence
    - Future WebSocket streaming (snapshot + delta)
    - Future persistence layer (SQLite / Postgres)

FUTURE-PROOFING
===============
  - `metadata: dict[str, Any]` fields absorb future structured fields without
    breaking the public API surface. New callers read named keys; old callers
    ignore them.
  - The `api_dict()` helper on each result produces a stable API-friendly
    representation that can be versioned independently of the Python object.
  - `schema_version` on ExecutionResult allows future migrations.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum, unique
from typing import Any, Optional

# ── Re-export ProcessResult from subprocess_mgr so callers have one import ──
# This preserves the subprocess_mgr.py architectural boundary (it owns process
# execution) while making ProcessResult a full member of the result hierarchy.
# subprocess_mgr.ProcessResult is the canonical definition; we do not redefine it.
try:
    from .subprocess_mgr import ProcessResult as ProcessResult  # noqa: F401
except ImportError:
    # Stand-alone / test context: define a minimal stub so result.py is
    # importable without the full runtime installed.
    @dataclass(frozen=True)
    class ProcessResult:  # type: ignore[no-redef]
        """
        Typed result from a completed subprocess invocation.

        This stub is used only when subprocess_mgr is not available.
        In the full runtime, subprocess_mgr.ProcessResult is the canonical type.
        """
        returncode:       int
        stdout:           bytes
        stderr:           bytes
        args:             list[str]
        elapsed_s:        float
        timed_out:        bool          = False
        signal_name:      Optional[str] = None
        output_truncated: bool          = False

        @property
        def success(self) -> bool:
            return self.returncode == 0 and not self.timed_out

        @property
        def stdout_text(self) -> str:
            return self.stdout.decode("utf-8", errors="replace")

        @property
        def stderr_text(self) -> str:
            return self.stderr.decode("utf-8", errors="replace")


__all__ = [
    # Re-exported from subprocess_mgr
    "ProcessResult",

    # Enumerations
    "ResultStatus",
    "VerificationStatus",
    "ObserveTerminationReason",

    # Core result types
    "Result",
    "FailureResult",
    "BuildResult",
    "FlashResult",
    "ObserveResult",
    "SimulationArtifact",
    "SimulationResult",
    "ExecutionResult",

    # Builder
    "ExecutionResultBuilder",

    # Helpers
    "result_from_process",
    "make_timeout_result",
    "make_cancelled_result",
]


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────────────────────────────────────

@unique
class ResultStatus(str, Enum):
    """
    Terminal disposition of any runtime operation.

    String-valued so enum members survive JSON round-trips without a custom
    encoder (``json.dumps({"status": ResultStatus.SUCCESS})`` → works).

    ``PARTIAL`` is a first-class status for operations that produced useful
    output but did not reach a clean terminal state — e.g., an observe stage
    that captured 50 lines before a USB disconnect. The engine can distinguish
    this from FAILED (no output at all) when deciding on retry semantics.
    """
    SUCCESS   = "SUCCESS"    # Operation completed successfully
    FAILED    = "FAILED"     # Operation failed; details in FailureResult
    TIMEOUT   = "TIMEOUT"    # Operation exceeded its time budget
    CANCELLED = "CANCELLED"  # Operation was externally cancelled
    PARTIAL   = "PARTIAL"    # Produced useful output but did not complete cleanly
    SKIPPED   = "SKIPPED"    # Stage was never reached (e.g., build failed → flash skipped)


@unique
class VerificationStatus(str, Enum):
    """
    Flash verification outcome.

    Three-valued because ``not verified`` has two distinct meanings:
    the tool verified and it FAILED (hardware problem), vs. the tool was
    run with verification disabled (SKIPPED). These require different
    retry decisions.
    """
    PASSED  = "PASSED"   # Tool verified flash contents match binary
    FAILED  = "FAILED"   # Tool verified and found a mismatch
    SKIPPED = "SKIPPED"  # Verification was not requested or not supported


@unique
class ObserveTerminationReason(str, Enum):
    """
    Why the serial observation stage ended.

    Drives engine retry decisions:
      SUCCESS_PATTERN → stage succeeded cleanly, no retry needed
      TIMEOUT         → device may need replan (firmware not printing expected output)
      FAILURE_PATTERN → device explicitly signalled failure; escalate
      CANCELLED       → external cancel; propagate cancellation
      RECONNECT_FAILED → USB lost and could not be recovered; retry may help
      ERROR           → unexpected exception in the serial layer
    """
    SUCCESS_PATTERN  = "SUCCESS_PATTERN"   # Configured success string matched
    FAILURE_PATTERN  = "FAILURE_PATTERN"   # Configured failure string matched
    TIMEOUT          = "TIMEOUT"           # Observation window elapsed
    CANCELLED        = "CANCELLED"         # Task was externally cancelled
    RECONNECT_FAILED = "RECONNECT_FAILED"  # Serial reconnect budget exhausted
    ERROR            = "ERROR"             # Unexpected serial runtime error


# ─────────────────────────────────────────────────────────────────────────────
# Base Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Result:
    """
    Immutable base type for all runtime operation results.

    Every result carries:
      - success:     shorthand boolean; True iff status == SUCCESS
      - status:      full terminal disposition (see ResultStatus)
      - timestamp:   wall-clock epoch seconds at result creation
      - duration_ms: operation duration in integer milliseconds
                     (int avoids float precision loss across serialisation)
      - message:     human-readable one-line summary (always set, never empty)
      - metadata:    escape hatch for structured fields added in future without
                     breaking the public API surface

    Callers should check ``result.success`` for fast-path logic and inspect
    ``result.status`` when they need to distinguish TIMEOUT vs FAILED vs
    CANCELLED.

    Not intended to be instantiated directly — use a concrete subtype.
    """
    success:     bool
    status:      ResultStatus
    timestamp:   float = field(default_factory=time.time)
    duration_ms: int   = 0
    message:     str   = ""
    metadata:    dict[str, Any] = field(default_factory=dict)

    def api_dict(self) -> dict[str, Any]:
        """
        Stable, API-friendly representation of this result.

        Enums are emitted as their .value string. Bytes fields are excluded
        (subclasses handle their own byte-field serialisation).

        This method is the canonical way to produce a JSON-safe dict for
        WebSocket streaming, REST responses, or persistence. It is NOT
        ``dataclasses.asdict()`` — that recurses into nested objects in ways
        that are hard to version-stabilise.
        """
        return {
            "success":     self.success,
            "status":      self.status.value,
            "timestamp":   self.timestamp,
            "duration_ms": self.duration_ms,
            "message":     self.message,
            "metadata":    self.metadata,
        }

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} "
            f"status={self.status.value} "
            f"duration={self.duration_ms}ms "
            f"msg={self.message!r:.60}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# FailureResult — companion object for any failed Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FailureResult:
    """
    Structured failure record, composable with any Result subtype.

    Not a Result subclass. It is a *companion* that any result carries in its
    ``failure`` field when its status is not SUCCESS.

    Rationale for companion pattern (not subclass):
      - Avoids an inheritance diamond across all result subtypes.
      - Failure fields are always ``None`` on success — no wasted slots.
      - A single FailureResult shape is uniform across all stages, making
        it easy to aggregate failures for telemetry and AI diagnosis.
      - Composable: future result types get failure support for free.

    Design contract:
      - ``category``  is the SAME string vocabulary as FailureCategory in
        retry.py and failure_classifier.py. result.py does NOT import those
        modules (to avoid circular dependencies), so ``category`` is a plain
        string here. Callers cast to FailureCategory when they need enum ops.
      - ``retryable`` is advisory only. The RetryEngine makes the final call.
      - ``raw_output`` is the first 4096 chars of combined stdout+stderr from
        the failing subprocess, for diagnostic display. Truncated to prevent
        the result object from becoming unbounded in size.
    """
    category:    str              # FailureCategory.value string
    message:     str              # One-line human summary
    retryable:   bool             # Advisory: was this a transient failure?
    stage:       str              # "plan" | "build" | "flash" | "observe" | "simulate" | "engine"
    exception_type: Optional[str] = None   # type(exc).__name__
    raw_output:  Optional[str]    = None   # Truncated combined output (≤ 4096 chars)
    metadata:    dict[str, Any]   = field(default_factory=dict)

    _MAX_RAW_OUTPUT = 4096

    def __post_init__(self) -> None:
        # Enforce raw_output bound even if caller forgets to truncate
        if self.raw_output and len(self.raw_output) > self._MAX_RAW_OUTPUT:
            object.__setattr__(
                self,
                "raw_output",
                self.raw_output[:self._MAX_RAW_OUTPUT] + "\n[TRUNCATED]",
            )

    def api_dict(self) -> dict[str, Any]:
        return {
            "category":       self.category,
            "message":        self.message,
            "retryable":      self.retryable,
            "stage":          self.stage,
            "exception_type": self.exception_type,
            "raw_output":     self.raw_output,
            "metadata":       self.metadata,
        }

    def __repr__(self) -> str:
        return (
            f"<FailureResult [{self.category}] stage={self.stage} "
            f"retryable={self.retryable} "
            f"msg={self.message!r:.60}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BuildResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BuildResult(Result):
    """
    Result of the build stage (build_firmware.py).

    Produced after the toolchain (idf.py, arduino-cli, cmake, pio) runs and
    either produces a binary artifact or fails.

    Fields
    ------
    firmware_path    : Absolute path to the compiled binary (.bin/.elf/.hex).
                       None on failure. The engine writes this to
                       session.context.build_artifact before advancing.
    build_size_bytes : Final binary size in bytes. Useful for tracking
                       firmware growth across iterations. 0 on failure.
    platform         : Toolchain platform string: "esp-idf" | "arduino-cli" |
                       "platformio" | "cmake-arm" | etc. Set by the builder.
    board            : Target board identifier: "esp32", "uno", "nucleo-f446re".
                       Matches session.context.target_id semantics.
    toolchain_version: Version string of the primary tool (idf.py --version,
                       arduino-cli version, etc.). Used for reproducibility
                       tracking and debugging version-specific failures.
    warnings_count   : Number of compiler warnings emitted. Does not affect
                       success/failure but is surfaced for AI diagnostic agents
                       that may correlate warnings with runtime misbehaviour.
    process_result   : The raw ProcessResult from the build subprocess, if
                       available. None for plan-level failures that never
                       reached the toolchain.
    failure          : Populated on any non-SUCCESS status.
    """
    firmware_path:     Optional[str]           = None
    build_size_bytes:  int                     = 0
    platform:          str                     = ""
    board:             str                     = ""
    toolchain_version: str                     = ""
    warnings_count:    int                     = 0
    process_result:    Optional[ProcessResult] = None
    failure:           Optional[FailureResult] = None

    def api_dict(self) -> dict[str, Any]:
        base = super().api_dict()
        base.update({
            "firmware_path":     self.firmware_path,
            "build_size_bytes":  self.build_size_bytes,
            "platform":          self.platform,
            "board":             self.board,
            "toolchain_version": self.toolchain_version,
            "warnings_count":    self.warnings_count,
            # process_result: stdout/stderr are bytes — include text repr only
            "process_exit_code": (
                self.process_result.returncode
                if self.process_result else None
            ),
            "process_timed_out": (
                self.process_result.timed_out
                if self.process_result else None
            ),
            "failure":           self.failure.api_dict() if self.failure else None,
        })
        return base


# ─────────────────────────────────────────────────────────────────────────────
# FlashResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FlashResult(Result):
    """
    Result of the flash stage (flash_firmware.py).

    Produced after esptool.py, OpenOCD, arduino-cli upload, or avrdude
    completes (or fails) writing the compiled binary to the target device.

    Fields
    ------
    port               : Serial port used for flashing ("/dev/ttyUSB0", "COM3").
    board              : Target board identifier (matches BuildResult.board).
    flash_duration_ms  : Time spent in the flash write phase (not including
                         subprocess startup or verification). 0 on failure.
    verification_status: Whether the tool verified the flash contents.
                         See VerificationStatus for the three-valued meaning.
    bytes_written      : Number of bytes confirmed written to flash. 0 if the
                         tool did not report this (tool-dependent).
    tool               : Flash tool used: "esptool" | "openocd" | "arduino-cli"
                         | "avrdude". Allows failure_classifier to apply
                         tool-specific patterns without stage-level coupling.
    tool_version       : Version string of the flash tool.
    process_result     : Raw ProcessResult from the flash subprocess.
    failure            : Populated on any non-SUCCESS status.

    Note on partial success: If the flash write completed but verification
    FAILED, status is FAILED and verification_status is FAILED. If the write
    itself was interrupted (USB disconnect mid-write), status is FAILED and
    failure.category will be DEVICE_DISCONNECTED. These are distinct because
    the retry strategies differ.
    """
    port:                str                     = ""
    board:               str                     = ""
    flash_duration_ms:   int                     = 0
    verification_status: VerificationStatus      = VerificationStatus.SKIPPED
    bytes_written:       int                     = 0
    tool:                str                     = ""
    tool_version:        str                     = ""
    process_result:      Optional[ProcessResult] = None
    failure:             Optional[FailureResult] = None

    def api_dict(self) -> dict[str, Any]:
        base = super().api_dict()
        base.update({
            "port":                self.port,
            "board":               self.board,
            "flash_duration_ms":   self.flash_duration_ms,
            "verification_status": self.verification_status.value,
            "bytes_written":       self.bytes_written,
            "tool":                self.tool,
            "tool_version":        self.tool_version,
            "process_exit_code":   (
                self.process_result.returncode
                if self.process_result else None
            ),
            "process_timed_out":   (
                self.process_result.timed_out
                if self.process_result else None
            ),
            "failure":             self.failure.api_dict() if self.failure else None,
        })
        return base


# ─────────────────────────────────────────────────────────────────────────────
# ObserveResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ObserveResult(Result):
    """
    Result of the serial observation stage (serial_runtime.py / observer).

    Produced after the serial monitor session ends, regardless of how it
    ended. This is intentionally a "terminal snapshot" — not a streaming
    result (that's the job of SerialObservation objects during live monitoring).

    Fields
    ------
    lines_captured       : Total device lines received during the session.
                           Used to distinguish "timed out with output" from
                           "timed out silently" — different diagnostics.
    reconnect_count      : How many USB reconnects occurred during the session.
                           > 0 indicates USB instability that may need
                           escalation to a replan with different tolerances.
    monitoring_duration_ms: Actual wall time the serial port was open and
                           reading. May be less than duration_ms if setup/
                           teardown time is included in the outer duration.
    termination_reason   : Why the observation stage ended.
                           See ObserveTerminationReason for full semantics.
    matched_pattern      : The exact string that matched the success or
                           failure pattern, if termination_reason is
                           SUCCESS_PATTERN or FAILURE_PATTERN. None otherwise.
                           Useful for AI agents diagnosing what the device
                           actually printed.
    port                 : The serial port that was monitored (may have
                           changed from the configured port if reconnect
                           occurred and the device re-enumerated on a new
                           /dev/ttyUSBN index).
    failure              : Populated on FAILED, TIMEOUT (if treated as failure),
                           RECONNECT_FAILED, or ERROR.

    Note on PARTIAL status: If lines_captured > 0 but termination_reason is
    TIMEOUT, status is set to PARTIAL (not FAILED). The engine uses this to
    decide whether to escalate to a replan (device is not printing the expected
    success pattern) vs. retry the observe stage (USB glitch).
    """
    lines_captured:         int                          = 0
    reconnect_count:        int                          = 0
    monitoring_duration_ms: int                          = 0
    termination_reason:     ObserveTerminationReason     = ObserveTerminationReason.TIMEOUT
    matched_pattern:        Optional[str]                = None
    port:                   str                          = ""
    failure:                Optional[FailureResult]      = None

    def api_dict(self) -> dict[str, Any]:
        base = super().api_dict()
        base.update({
            "lines_captured":          self.lines_captured,
            "reconnect_count":         self.reconnect_count,
            "monitoring_duration_ms":  self.monitoring_duration_ms,
            "termination_reason":      self.termination_reason.value,
            "matched_pattern":         self.matched_pattern,
            "port":                    self.port,
            "failure":                 self.failure.api_dict() if self.failure else None,
        })
        return base


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SimulationArtifact:
    """Files and identity associated with one generated simulation project."""

    project_path: str
    firmware_path: str
    simulation_id: str
    timestamp: float = field(default_factory=time.time)

    def api_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "firmware_path": self.firmware_path,
            "simulation_id": self.simulation_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class SimulationResult(Result):
    """Terminal result from a virtual hardware simulation attempt."""

    simulation_artifact: Optional[SimulationArtifact] = None
    board: str = ""
    serial_output: list[str] = field(default_factory=list)
    lines_captured: int = 0
    output_truncated: bool = False
    simulation_duration_ms: int = 0
    process_result: Optional[ProcessResult] = None
    failure: Optional[FailureResult] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "serial_output", list(self.serial_output))

    def api_dict(self) -> dict[str, Any]:
        base = super().api_dict()
        base.update({
            "simulation_artifact": (
                self.simulation_artifact.api_dict()
                if self.simulation_artifact else None
            ),
            "board": self.board,
            "serial_output": list(self.serial_output),
            "lines_captured": self.lines_captured,
            "output_truncated": self.output_truncated,
            "simulation_duration_ms": self.simulation_duration_ms,
            "process_exit_code": (
                self.process_result.returncode
                if self.process_result else None
            ),
            "process_timed_out": (
                self.process_result.timed_out
                if self.process_result else None
            ),
            "failure": self.failure.api_dict() if self.failure else None,
        })
        return base


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExecutionResult(Result):
    """
    Final record of a complete engine execution run.

    Produced by engine.py after the full Plan → Build → Flash → Observe
    pipeline terminates, regardless of outcome. The engine NEVER raises to
    the caller — all outcomes are reflected here.

    This supersedes the thin ``ExecutionResult`` currently defined in engine.py.
    When migrating, replace engine.py's dataclass with an import of this one.

    Fields
    ------
    session_id     : UUID of the session that produced this result.
    final_state    : SessionState.name of the terminal state ("COMPLETED",
                     "FAILED", "CANCELLED"). String to avoid importing
                     session.py at the result layer.
    retry_count    : Number of RETRY + ESCALATE decisions consumed.
                     Does NOT count policy-ABORT decisions (per [FIX-R2]).
    total_wait_ms  : Total wall time spent in retry backoff (integer ms).
    failure_category: FailureCategory.value of the last recorded failure.
                     None if the run completed without any failure.
    artifacts      : Paths to all files produced by this execution (binaries,
                     map files, etc.). Populated from BuildResult.firmware_path
                     and any other tool outputs. JSON-serialisable list.
    build_result   : BuildResult if the build stage was reached. None if the
                     pipeline failed before building.
    flash_result   : FlashResult if the flash stage was reached.
    observe_result : ObserveResult if the observe stage was reached.
    failure        : Populated if final_state != "COMPLETED".
    schema_version : Integer bumped when the shape of ExecutionResult changes
                     in a breaking way. Allows future migration code to branch
                     on version when deserialising historical records.
    """
    session_id:       str                          = ""
    final_state:      str                          = "FAILED"
    retry_count:      int                          = 0
    total_wait_ms:    int                          = 0
    failure_category: Optional[str]               = None
    artifacts:        list[str]                   = field(default_factory=list)
    build_result:     Optional[BuildResult]        = None
    flash_result:     Optional[FlashResult]        = None
    observe_result:   Optional[ObserveResult]      = None
    simulation_result: Optional[SimulationResult]  = None
    failure:          Optional[FailureResult]      = None
    schema_version:   int                          = 1

    def api_dict(self) -> dict[str, Any]:
        base = super().api_dict()
        base.update({
            "session_id":       self.session_id,
            "final_state":      self.final_state,
            "retry_count":      self.retry_count,
            "total_wait_ms":    self.total_wait_ms,
            "failure_category": self.failure_category,
            "artifacts":        list(self.artifacts),
            "build_result":     self.build_result.api_dict() if self.build_result else None,
            "flash_result":     self.flash_result.api_dict() if self.flash_result else None,
            "observe_result":   self.observe_result.api_dict() if self.observe_result else None,
            "simulation_result": self.simulation_result.api_dict() if self.simulation_result else None,
            "failure":          self.failure.api_dict() if self.failure else None,
            "schema_version":   self.schema_version,
        })
        return base

    def __repr__(self) -> str:
        status = "OK" if self.success else self.final_state
        return (
            f"<ExecutionResult {status} "
            f"session={self.session_id[:8] if self.session_id else '?'} "
            f"duration={self.duration_ms}ms "
            f"retries={self.retry_count}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionResultBuilder
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionResultBuilder:
    """
    Mutable builder that accumulates stage results and produces an
    immutable ExecutionResult at the end.

    The engine assembles an execution result across multiple stages and
    callback points. Using a builder avoids passing partially-constructed
    frozen dataclasses through the pipeline.

    Usage (inside engine.py)::

        builder = ExecutionResultBuilder(session_id=session.id)
        builder.record_build(build_result)
        builder.record_flash(flash_result)
        builder.record_observe(observe_result)
        result = builder.build(session=session, retry_stats=stats)

    The builder owns no state machine logic — it is a pure data accumulator.
    The engine still drives all transitions.
    """

    def __init__(
        self,
        session_id: str,
        *,
        start_time: Optional[float] = None,
    ) -> None:
        self._session_id   = session_id
        self._start        = start_time or time.time()
        self._build_result:   Optional[BuildResult]   = None
        self._flash_result:   Optional[FlashResult]   = None
        self._observe_result: Optional[ObserveResult] = None
        self._simulation_result: Optional[SimulationResult] = None
        self._failure:        Optional[FailureResult] = None

    def record_build(self, result: BuildResult) -> None:
        """Store the build stage result."""
        self._build_result = result

    def record_flash(self, result: FlashResult) -> None:
        """Store the flash stage result."""
        self._flash_result = result

    def record_observe(self, result: ObserveResult) -> None:
        """Store the observe stage result."""
        self._observe_result = result

    def record_simulation(self, result: SimulationResult) -> None:
        """Store the simulation stage result."""
        # fix: retain simulation output in the top-level execution record.
        self._simulation_result = result

    def record_failure(self, failure: FailureResult) -> None:
        """Record the terminal failure (overwrites any previous failure)."""
        self._failure = failure

    def build(
        self,
        *,
        final_state:      str,
        retry_count:      int               = 0,
        total_wait_ms:    int               = 0,
        failure_category: Optional[str]     = None,
        message:          str               = "",
        metadata:         Optional[dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Produce the final immutable ExecutionResult.

        This is the ONLY place where ``duration_ms`` is computed — from
        ``time.time() - self._start``. All other duration_ms values on
        sub-results are computed by their respective stage producers.

        Args:
            final_state:      SessionState.name of the terminal state.
            retry_count:      RETRY + ESCALATE decisions consumed.
            total_wait_ms:    Total actual backoff time in milliseconds.
            failure_category: FailureCategory.value of the last failure.
            message:          Human-readable one-line summary.
            metadata:         Arbitrary structured fields (session summary,
                              state history, etc.) for API consumers.
        """
        now          = time.time()
        duration_ms  = int((now - self._start) * 1000)
        success      = (final_state == "COMPLETED")
        status       = ResultStatus.SUCCESS if success else (
            ResultStatus.CANCELLED if final_state == "CANCELLED"
            else ResultStatus.FAILED
        )

        # Collect artifact paths from stage results
        artifacts: list[str] = []
        if self._build_result and self._build_result.firmware_path:
            artifacts.append(self._build_result.firmware_path)

        if not message:
            message = (
                f"Session {self._session_id[:8]} {final_state}"
                f" in {duration_ms}ms"
                + (f" after {retry_count} retries" if retry_count else "")
            )

        return ExecutionResult(
            # Base Result fields
            success      = success,
            status       = status,
            timestamp    = now,
            duration_ms  = duration_ms,
            message      = message,
            metadata     = metadata or {},

            # ExecutionResult-specific fields
            session_id       = self._session_id,
            final_state      = final_state,
            retry_count      = retry_count,
            total_wait_ms    = total_wait_ms,
            failure_category = failure_category,
            artifacts        = artifacts,
            build_result     = self._build_result,
            flash_result     = self._flash_result,
            observe_result   = self._observe_result,
            simulation_result = self._simulation_result,
            failure          = self._failure,
            schema_version   = 1,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Factory helpers
# ─────────────────────────────────────────────────────────────────────────────

def result_from_process(
    proc: ProcessResult,
    *,
    stage: str,
    failure_category: str = "UNKNOWN",
    retryable: bool = True,
    start_time: Optional[float] = None,
) -> tuple[ResultStatus, Optional[FailureResult]]:
    """
    Derive a ResultStatus and optional FailureResult from a ProcessResult.

    This is a pure helper — it does not construct the full stage result
    (that is the stage's responsibility). It extracts the common logic of
    "did this subprocess succeed, and if not, what failed?" so builders
    for BuildResult, FlashResult, etc. don't repeat this logic.

    Returns a ``(status, failure_or_none)`` tuple.

    Usage (inside flash_firmware.py)::

        proc = await subprocess_mgr.run(config)
        status, failure = result_from_process(proc, stage="flash",
                                              failure_category=category.value)
        return FlashResult(
            success=status == ResultStatus.SUCCESS,
            status=status,
            ...
            failure=failure,
        )
    """
    if proc.success:
        return ResultStatus.SUCCESS, None

    if proc.timed_out:
        status = ResultStatus.TIMEOUT
        message = (
            f"Command {proc.args[0]!r} timed out after "
            f"{proc.elapsed_s:.1f}s in stage '{stage}'"
        )
    else:
        status = ResultStatus.FAILED
        combined = (proc.stderr_text + " " + proc.stdout_text).strip()
        short    = combined[:200] if combined else f"exit code {proc.returncode}"
        message  = (
            f"Command {proc.args[0]!r} failed (exit {proc.returncode}) "
            f"in stage '{stage}': {short}"
        )

    raw = (proc.stderr_text + proc.stdout_text)
    failure = FailureResult(
        category       = failure_category,
        message        = message,
        retryable      = retryable,
        stage          = stage,
        exception_type = "ExecutionError" if not proc.timed_out else "ProcessTimeoutError",
        raw_output     = raw[:FailureResult._MAX_RAW_OUTPUT],
    )
    return status, failure


def make_timeout_result(
    result_type: type,
    *,
    stage: str,
    timeout_s: float,
    failure_category: str = "FLASH_TIMEOUT",
    start_time: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """
    Construct a typed timeout result for any Result subtype.

    Convenience factory for the common case where a stage's asyncio.wait_for
    fires. Returns an instance of ``result_type`` with STATUS=TIMEOUT and
    a pre-populated FailureResult.

    Usage::

        if timed_out:
            return make_timeout_result(
                FlashResult,
                stage="flash",
                timeout_s=config.flash_timeout_s,
                failure_category=FailureCategory.FLASH_TIMEOUT.value,
                port=target.port,
                board=target.target_id,
            )
    """
    message = (
        f"Stage '{stage}' timed out after {timeout_s:.1f}s"
    )
    failure = FailureResult(
        category       = failure_category,
        message        = message,
        retryable      = True,
        stage          = stage,
        exception_type = "asyncio.TimeoutError",
    )
    now = time.time()
    start = start_time or now
    return result_type(
        success      = False,
        status       = ResultStatus.TIMEOUT,
        timestamp    = now,
        duration_ms  = int((now - start) * 1000),
        message      = message,
        failure      = failure,
        **kwargs,
    )


def make_cancelled_result(
    result_type: type,
    *,
    stage: str,
    start_time: Optional[float] = None,
    **kwargs: Any,
) -> Any:
    """
    Construct a typed cancellation result for any Result subtype.

    Usage::

        except asyncio.CancelledError:
            return make_cancelled_result(FlashResult, stage="flash",
                                         port=target.port)
    """
    message = f"Stage '{stage}' was cancelled"
    failure = FailureResult(
        category       = "UNKNOWN",
        message        = message,
        retryable      = False,
        stage          = stage,
        exception_type = "asyncio.CancelledError",
    )
    now   = time.time()
    start = start_time or now
    return result_type(
        success      = False,
        status       = ResultStatus.CANCELLED,
        timestamp    = now,
        duration_ms  = int((now - start) * 1000),
        message      = message,
        failure      = failure,
        **kwargs,
    )
