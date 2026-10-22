"""
runtime/failure_classifier.py
==============================
PromptForge AI — Deterministic Failure Classifier

Converts raw failure signals into structured FailureClassification objects.

CONTRACT (architectural invariants):
  - MUST NOT perform retries
  - MUST NOT update session state
  - MUST NOT modify orchestration
  - MUST NOT call the engine
  - MUST NOT own recovery logic
  - MUST be deterministic: same inputs → same output, always
  - MUST be replay-safe: no side effects, no I/O, no randomness
  - MUST NOT use LLMs, plugins, or event buses

All classification is pattern-driven and stage-aware.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

@unique
class FailureCategory(str, Enum):
    """Canonical taxonomy of failure kinds recognised by PromptForge."""

    # --- Build / Compilation ---
    BUILD_ERROR         = "BUILD_ERROR"          # Generic build pipeline failure
    COMPILATION_ERROR   = "COMPILATION_ERROR"    # Compiler (gcc/g++/clang) error
    LINKER_ERROR        = "LINKER_ERROR"         # Linker / undefined-reference error
    TOOLCHAIN_MISSING   = "TOOLCHAIN_MISSING"    # Compiler or tool not on PATH

    # --- Flash / Programming ---
    PORT_BUSY           = "PORT_BUSY"            # Serial/USB port locked by another process
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"  # Device vanished before/during flash
    FLASH_TIMEOUT       = "FLASH_TIMEOUT"        # esptool/OpenOCD did not complete in time
    INVALID_FIRMWARE    = "INVALID_FIRMWARE"     # Binary rejected (bad magic, wrong chip)
    TRANSIENT_USB_FAILURE = "TRANSIENT_USB_FAILURE"  # USB enumeration glitch, may self-heal

    # --- Serial / Observe ---
    OBSERVE_TIMEOUT     = "OBSERVE_TIMEOUT"      # No output within expected window
    SERIAL_PERMISSION_ERROR = "SERIAL_PERMISSION_ERROR"  # /dev/ttyUSB* permission denied
    WATCHDOG_RESET      = "WATCHDOG_RESET"       # Firmware rebooted via WDT
    SERIAL_FRAMING_ERROR = "SERIAL_FRAMING_ERROR"  # Corrupt bytes / baud mismatch

    # --- Catch-all ---
    UNKNOWN             = "UNKNOWN"              # Could not classify; needs human review


@unique
class FailureSeverity(str, Enum):
    """
    Operational severity — drives alerting and retry budget decisions upstream.
    The classifier emits severity; the engine decides what to do with it.
    """
    INFO     = "INFO"      # Informational; no action required
    WARNING  = "WARNING"   # Degraded but may self-recover
    ERROR    = "ERROR"     # Operation failed; retry may help
    CRITICAL = "CRITICAL"  # Unrecoverable without human intervention


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FailureClassification:
    """
    Immutable, serialisable classification result.

    Fields
    ------
    category  : Canonical failure kind.
    severity  : Operational impact level.
    retryable : Whether the engine should attempt a retry.
                True does NOT mandate a retry — that decision belongs to retry.py.
    confidence: Float in [0.0, 1.0]. 1.0 = definitive pattern match.
                Used by callers to decide whether to log/escalate ambiguous cases.
    message   : Human-readable summary of why this classification was chosen.
    raw_signal: Optional original text/repr that triggered classification
                (useful for debugging and replay).
    """
    category:   FailureCategory
    severity:   FailureSeverity
    retryable:  bool
    confidence: float                      # [0.0, 1.0]
    message:    str
    raw_signal: Optional[str] = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0.0, 1.0], got {self.confidence}"
            )


# ---------------------------------------------------------------------------
# Internal pattern tables
# ---------------------------------------------------------------------------
# Each rule is a (compiled_regex, FailureCategory, FailureSeverity, retryable, confidence)
# tuple evaluated in order.  First match wins — place more-specific rules first.
#
# Patterns are intentionally kept *literal* (no AI, no fuzzy matching).  When
# a new tool emits a new error, add a row here.

_PatternRule = tuple[re.Pattern[str], FailureCategory, FailureSeverity, bool, float]


def _r(pattern: str) -> re.Pattern[str]:
    """Compile a case-insensitive pattern once at import time."""
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


# --- Toolchain / environment rules (tool-agnostic, checked first) ----------
_TOOLCHAIN_RULES: list[_PatternRule] = [
    (_r(r"command not found"),          FailureCategory.TOOLCHAIN_MISSING, FailureSeverity.CRITICAL, False, 0.97),
    # Tool name before "no such file" — require non-word char before name to avoid
    # matching substrings like "ld" inside "could" or "failed"
    (_r(r"(?<![a-z])(gcc|g\+\+|ld|ar|objcopy|esptool|openocd|avr-gcc|arm-none-eabi|wokwi-cli)(?!\w).{0,60}no such file or directory"),
                                        FailureCategory.TOOLCHAIN_MISSING, FailureSeverity.CRITICAL, False, 0.95),
    # Tool name after "no such file" (original esptool placement)
    (_r(r"no such file or directory.{0,60}(?<![a-z])(gcc|g\+\+|ld|ar|objcopy|esptool|openocd|avr-gcc|arm-none-eabi|wokwi-cli)(?!\w)"),
                                        FailureCategory.TOOLCHAIN_MISSING, FailureSeverity.CRITICAL, False, 0.95),
    # "esptool: not installed" OR "esptool is not installed" OR "esptool: is not installed"
    (_r(r"(esptool|openocd|avrdude|pio|wokwi-cli)\s*(:?\s*is)?\s*not\s+(installed|found)"),
                                        FailureCategory.TOOLCHAIN_MISSING, FailureSeverity.CRITICAL, False, 0.97),
    (_r(r"pip install|apt(-get)? install|brew install"),
                                        FailureCategory.TOOLCHAIN_MISSING, FailureSeverity.ERROR,    False, 0.70),
]

# --- Build / Compilation rules (BUILD stage) --------------------------------
_BUILD_RULES: list[_PatternRule] = [
    # Linker errors — must precede generic compilation patterns
    (_r(r"undefined reference to"),     FailureCategory.LINKER_ERROR,    FailureSeverity.ERROR,    False, 0.99),
    (_r(r"ld returned \d+ exit status"), FailureCategory.LINKER_ERROR,   FailureSeverity.ERROR,    False, 0.98),
    (_r(r"multiple definition of"),     FailureCategory.LINKER_ERROR,    FailureSeverity.ERROR,    False, 0.97),
    (_r(r"cannot find -l\S+"),          FailureCategory.LINKER_ERROR,    FailureSeverity.ERROR,    False, 0.96),
    (_r(r"relocation truncated to fit"), FailureCategory.LINKER_ERROR,   FailureSeverity.ERROR,    False, 0.95),

    # Compiler errors
    (_r(r"error:\s.+\[-W"),             FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR,  False, 0.92),
    (_r(r":\s*error:\s"),               FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR,  False, 0.90),
    (_r(r"fatal error:"),               FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR,  False, 0.95),
    (_r(r"#include\s*[<\"].+[>\"]\s*.*No such file"), FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR, False, 0.96),
    (_r(r"expected\s+(unqualified-id|';'|')"),         FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR, False, 0.88),
    (_r(r"implicit declaration of function"),           FailureCategory.COMPILATION_ERROR, FailureSeverity.ERROR, False, 0.90),

    # PlatformIO-specific build failure
    (_r(r"ERROR: BuildError"),          FailureCategory.BUILD_ERROR,     FailureSeverity.ERROR,    False, 0.97),
    (_r(r"\*\*\* \[.+\] Error \d+"),   FailureCategory.BUILD_ERROR,     FailureSeverity.ERROR,    False, 0.93),
    (_r(r"make\[\d+\]:.+Error \d+"),   FailureCategory.BUILD_ERROR,     FailureSeverity.ERROR,    False, 0.92),
    (_r(r"cmake.*error",),             FailureCategory.BUILD_ERROR,     FailureSeverity.ERROR,    False, 0.88),
]

# --- Flash / Programming rules (FLASH stage) --------------------------------
_FLASH_RULES: list[_PatternRule] = [
    # Port / device access
    (_r(r"(permission denied|access denied).*(tty|serial|usb|com\d)"),
                                        FailureCategory.SERIAL_PERMISSION_ERROR, FailureSeverity.ERROR, False, 0.97),
    (_r(r"(device|port|serial).*(busy|in use|locked)"),
                                        FailureCategory.PORT_BUSY,       FailureSeverity.WARNING,  True,  0.95),
    (_r(r"resource temporarily unavailable"),
                                        FailureCategory.PORT_BUSY,       FailureSeverity.WARNING,  True,  0.88),
    (_r(r"(no such device|could not open port|failed to open port)"),
                                        FailureCategory.DEVICE_DISCONNECTED, FailureSeverity.ERROR, True, 0.94),
    (_r(r"(device disconnected|usb device (not found|removed|lost|disconnect))"),
                                        FailureCategory.DEVICE_DISCONNECTED, FailureSeverity.ERROR, True,  0.97),
    (_r(r"errno 19"),                   FailureCategory.DEVICE_DISCONNECTED, FailureSeverity.ERROR, True,  0.85),

    # Transient USB enumeration glitches
    (_r(r"(usb reset|device enumeration (failed|retry)|ioerror.*(usb|tty))"),
                                        FailureCategory.TRANSIENT_USB_FAILURE, FailureSeverity.WARNING, True, 0.88),
    (_r(r"(broken pipe|ioerror).*(serial|tty|usb)"),
                                        FailureCategory.TRANSIENT_USB_FAILURE, FailureSeverity.WARNING, True, 0.82),

    # Firmware / chip validation
    (_r(r"(wrong (chip|target)|invalid (image|firmware|binary)|bad magic)"),
                                        FailureCategory.INVALID_FIRMWARE, FailureSeverity.CRITICAL, False, 0.96),
    (_r(r"(chip mismatch|target mismatch)"),
                                        FailureCategory.INVALID_FIRMWARE, FailureSeverity.CRITICAL, False, 0.95),
    (_r(r"flash verification (failed|error)"),
                                        FailureCategory.INVALID_FIRMWARE, FailureSeverity.ERROR,    True,  0.90),

    # Timeouts — bidirectional: timeout word before OR after flash keyword
    (_r(r"(timed? ?out|timeout).*(flash|write|erase|connect|download|packet)"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.92),
    (_r(r"(flash|erase|write).*(timed? ?out|timeout)"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.92),
    # esptool: "Timed out waiting for packet header" / "A fatal error occurred: Timed out"
    (_r(r"timed? ?out waiting"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.90),
    (_r(r"a fatal error occurred"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.85),
    (_r(r"failed to connect to (esp|target|device)"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.87),
    (_r(r"(openocd|jtag).*(timeout|no response)"),
                                        FailureCategory.FLASH_TIMEOUT,   FailureSeverity.ERROR,    True,  0.88),
]

# --- Observe / Serial-monitor rules (OBSERVE stage) -------------------------
_OBSERVE_RULES: list[_PatternRule] = [
    # Watchdog — very specific, must precede generic serial-disconnect patterns
    # Covers: "Guru Meditation", "Task watchdog", "WDT reset", "rst:0x8 (WDT_RST)"
    (_r(r"(guru meditation|task watchdog|wdt.?reset|brownout detector|rst:0x[0-9a-f]+\s*\(wdt)"),
                                        FailureCategory.WATCHDOG_RESET,  FailureSeverity.ERROR,    True,  0.97),
    (_r(r"(ets_printf|backtrace|core dump).*wdt"),
                                        FailureCategory.WATCHDOG_RESET,  FailureSeverity.ERROR,    True,  0.88),

    # Serial framing / baud errors
    (_r(r"(framing error|parity error|break condition|serial.*noise)"),
                                        FailureCategory.SERIAL_FRAMING_ERROR, FailureSeverity.WARNING, True, 0.90),
    (_r(r"(garbled|corrupt|malformed).*(serial|uart|output)"),
                                        FailureCategory.SERIAL_FRAMING_ERROR, FailureSeverity.WARNING, True, 0.82),

    # Serial permission errors during observation (same pattern, different stage context)
    (_r(r"(permission denied|access denied).*(tty|serial|usb|com\d)"),
                                        FailureCategory.SERIAL_PERMISSION_ERROR, FailureSeverity.ERROR, False, 0.97),

    # Device disconnected mid-observation
    (_r(r"(device disconnected|usb device (not found|removed|lost)|serial.*closed unexpectedly)"),
                                        FailureCategory.DEVICE_DISCONNECTED, FailureSeverity.ERROR, True, 0.95),

    # Observe-specific timeouts
    (_r(r"(observation timeout|no output|expected output not received)"),
                                        FailureCategory.OBSERVE_TIMEOUT, FailureSeverity.ERROR,    True,  0.90),
]

# Map each stage name (lower-cased) to its primary rule table.
# Toolchain rules are always prepended; BUILD rules extend for flash context too.
_STAGE_RULES: dict[str, list[_PatternRule]] = {
    "build":   _TOOLCHAIN_RULES + _BUILD_RULES,
    "compile": _TOOLCHAIN_RULES + _BUILD_RULES,
    "flash":   _TOOLCHAIN_RULES + _FLASH_RULES,
    "program": _TOOLCHAIN_RULES + _FLASH_RULES,
    "observe": _TOOLCHAIN_RULES + _OBSERVE_RULES,
    "monitor": _TOOLCHAIN_RULES + _OBSERVE_RULES,
}

# Fallback: all rules in priority order (toolchain → build → flash → observe)
_ALL_RULES: list[_PatternRule] = (
    _TOOLCHAIN_RULES + _BUILD_RULES + _FLASH_RULES + _OBSERVE_RULES
)


# ---------------------------------------------------------------------------
# ProcessResult duck-type protocol
# ---------------------------------------------------------------------------
# We accept any object that exposes .returncode, .stdout, .stderr so that
# callers using subprocess_mgr.ProcessResult or a plain SimpleNamespace both
# work without an import dependency on subprocess_mgr.

class _ProcessResultLike:
    """Structural interface — for isinstance checks and type hints only."""
    returncode: int
    stdout: str
    stderr: str


def _has_process_result_shape(obj: object) -> bool:
    return (
        hasattr(obj, "returncode")
        and hasattr(obj, "stdout")
        and hasattr(obj, "stderr")
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_failure(
    *,
    exception:  Optional[BaseException] = None,
    stderr:     Optional[str] = None,
    stdout:     Optional[str] = None,
    process_result: Optional[object] = None,
    stage:      Optional[str] = None,
    tool:       Optional[str] = None,
) -> FailureClassification:
    """
    Deterministically classify a failure into a FailureClassification.

    Parameters
    ----------
    exception      : An exception raised during stage execution.
    stderr         : Raw stderr text from a subprocess.
    stdout         : Raw stdout text (some tools write errors to stdout).
    process_result : Any object with .returncode / .stdout / .stderr attributes
                     (e.g. subprocess_mgr.ProcessResult).
    stage          : Logical stage name ("build", "flash", "observe", …).
                     Used to apply stage-specific rule tables.
    tool           : Tool name ("esptool", "openocd", "avrdude", …).
                     Currently used for message enrichment; reserved for
                     future tool-specific rule tables.

    Returns
    -------
    FailureClassification — never raises, always returns a valid result.

    Design notes
    ------------
    - Deterministic: no randomness, no I/O, no external state.
    - Replay-safe: calling this function twice with the same arguments
      produces identical results.
    - Stage-aware: "permission denied" during OBSERVE is classified as
      SERIAL_PERMISSION_ERROR, not just a generic OS error.
    - The function never mutates any argument.
    """

    # ---- 1. Normalise inputs -----------------------------------------------

    stage_key = (stage or "").strip().lower()
    tool_label = (tool or "unknown-tool").strip().lower()

    # Unpack a ProcessResult if provided
    if process_result is not None and _has_process_result_shape(process_result):
        if stderr is None:
            stderr = getattr(process_result, "stderr", None) or ""
        if stdout is None:
            stdout = getattr(process_result, "stdout", None) or ""

    stderr = (stderr or "").strip()
    stdout = (stdout or "").strip()

    # ---- 2. Build a composite signal text for pattern matching -------------
    # We combine stderr + stdout because:
    #   - esptool writes some errors to stdout
    #   - avrdude mixes progress and errors on stderr
    # We keep stderr first so that higher-confidence stderr patterns win.

    combined = _join_signals(stderr, stdout)

    # If we have an exception, prepend its repr to the signal
    exc_text = ""
    if exception is not None:
        exc_text = _extract_exception_text(exception)
        combined = _join_signals(exc_text, combined)

    # ---- 3. Attempt exception-type short-circuit ----------------------------
    # Certain Python exceptions map unambiguously to categories regardless of
    # stage, so we classify them before consulting pattern tables.

    if exception is not None:
        fast = _classify_exception_type(exception, stage_key, combined)
        if fast is not None:
            return fast

    # ---- 4. Select rule table (stage-aware) ---------------------------------

    rules = _STAGE_RULES.get(stage_key, _ALL_RULES)

    # ---- 5. Pattern scan (first match wins) ---------------------------------

    for pattern, category, severity, retryable, confidence in rules:
        m = pattern.search(combined)
        if m:
            snippet = _excerpt(combined, m.start(), context=80)
            message = _build_message(category, stage, tool_label, snippet)
            return FailureClassification(
                category=category,
                severity=severity,
                retryable=retryable,
                confidence=confidence,
                message=message,
                raw_signal=combined[:512] or None,
            )

    # ---- 6. Heuristic: non-zero exit code with no pattern match -------------

    if process_result is not None and _has_process_result_shape(process_result):
        rc: int = getattr(process_result, "returncode", 0) or 0
        if rc != 0:
            return _classify_nonzero_exit(rc, stage_key, tool_label, combined)

    # ---- 7. Unknown — preserve signal for upstream logging ------------------

    return FailureClassification(
        category=FailureCategory.UNKNOWN,
        severity=FailureSeverity.ERROR,
        retryable=False,
        confidence=0.10,
        message=(
            f"Unrecognised failure in stage={stage!r} tool={tool_label!r}. "
            "Manual investigation required."
        ),
        raw_signal=combined[:512] or None,
    )


# ---------------------------------------------------------------------------
# Internal helpers (private — not part of the public API)
# ---------------------------------------------------------------------------

def _join_signals(*parts: str) -> str:
    """Join non-empty text segments with a newline separator."""
    return "\n".join(p for p in parts if p)


def _extract_exception_text(exc: BaseException) -> str:
    """Return a compact, single-line text representation of an exception."""
    return f"{type(exc).__name__}: {exc}"


def _excerpt(text: str, match_start: int, context: int = 80) -> str:
    """Return up to `context` characters around a match position."""
    start = max(0, match_start - context // 2)
    end   = min(len(text), match_start + context // 2)
    raw   = text[start:end].replace("\n", " ").strip()
    return raw[:context]


def _build_message(
    category: FailureCategory,
    stage:    Optional[str],
    tool:     str,
    snippet:  str,
) -> str:
    stage_label = stage or "unknown"
    return (
        f"[{category.value}] stage={stage_label!r} tool={tool!r} — {snippet}"
        if snippet
        else f"[{category.value}] stage={stage_label!r} tool={tool!r}"
    )


def _classify_exception_type(
    exc:       BaseException,
    stage_key: str,
    combined:  str,
) -> Optional[FailureClassification]:
    """
    Fast-path classification based on Python exception type.

    Returns None if the exception type does not map unambiguously to a category.
    """
    exc_type = type(exc).__name__

    # PermissionError → SERIAL_PERMISSION_ERROR (always non-retryable without
    # udev/group fix, but we surface it as retryable=False so the engine knows
    # not to burn its retry budget; human fix required).
    if isinstance(exc, PermissionError):
        return FailureClassification(
            category=FailureCategory.SERIAL_PERMISSION_ERROR,
            severity=FailureSeverity.ERROR,
            retryable=False,
            confidence=0.98,
            message=(
                f"PermissionError accessing serial/USB device "
                f"(check udev rules or group membership): {exc}"
            ),
            raw_signal=combined[:512] or None,
        )

    # FileNotFoundError in flash/observe context → DEVICE_DISCONNECTED
    if isinstance(exc, FileNotFoundError):
        if stage_key in ("flash", "program", "observe", "monitor"):
            return FailureClassification(
                category=FailureCategory.DEVICE_DISCONNECTED,
                severity=FailureSeverity.ERROR,
                retryable=True,
                confidence=0.88,
                message=(
                    f"FileNotFoundError during stage={stage_key!r} — "
                    f"device node likely missing or unplugged: {exc}"
                ),
                raw_signal=combined[:512] or None,
            )
        # In build context → TOOLCHAIN_MISSING
        if stage_key in ("build", "compile"):
            return FailureClassification(
                category=FailureCategory.TOOLCHAIN_MISSING,
                severity=FailureSeverity.CRITICAL,
                retryable=False,
                confidence=0.88,
                message=f"FileNotFoundError in build stage — toolchain binary missing: {exc}",
                raw_signal=combined[:512] or None,
            )

    # TimeoutError → stage-aware timeout category
    if isinstance(exc, TimeoutError):
        if stage_key in ("observe", "monitor"):
            return FailureClassification(
                category=FailureCategory.OBSERVE_TIMEOUT,
                severity=FailureSeverity.ERROR,
                retryable=True,
                confidence=0.95,
                message=f"TimeoutError during observation: {exc}",
                raw_signal=combined[:512] or None,
            )
        if stage_key in ("flash", "program"):
            return FailureClassification(
                category=FailureCategory.FLASH_TIMEOUT,
                severity=FailureSeverity.ERROR,
                retryable=True,
                confidence=0.95,
                message=f"TimeoutError during flash: {exc}",
                raw_signal=combined[:512] or None,
            )

    # OSError with errno 16 (EBUSY) → PORT_BUSY
    if isinstance(exc, OSError):
        import errno as _errno
        if getattr(exc, "errno", None) == _errno.EBUSY:
            return FailureClassification(
                category=FailureCategory.PORT_BUSY,
                severity=FailureSeverity.WARNING,
                retryable=True,
                confidence=0.97,
                message=f"EBUSY: port locked by another process: {exc}",
                raw_signal=combined[:512] or None,
            )
        # errno 19 ENODEV — device node disappeared
        if getattr(exc, "errno", None) == 19:
            return FailureClassification(
                category=FailureCategory.DEVICE_DISCONNECTED,
                severity=FailureSeverity.ERROR,
                retryable=True,
                confidence=0.93,
                message=f"ENODEV: device disconnected: {exc}",
                raw_signal=combined[:512] or None,
            )

    return None


def _classify_nonzero_exit(
    returncode: int,
    stage_key:  str,
    tool_label: str,
    combined:   str,
) -> FailureClassification:
    """
    Heuristic fallback when a process exits non-zero but no pattern matched.

    Maps common exit codes to plausible categories with low confidence so
    that callers know to treat this as a best-effort guess.
    """
    # esptool exit 2 typically means bad arguments / chip mismatch
    if tool_label in ("esptool", "esptool.py") and returncode == 2:
        return FailureClassification(
            category=FailureCategory.INVALID_FIRMWARE,
            severity=FailureSeverity.ERROR,
            retryable=False,
            confidence=0.60,
            message=(
                f"esptool exited with code 2 — likely argument error or chip mismatch "
                f"(stage={stage_key!r})"
            ),
            raw_signal=combined[:512] or None,
        )

    # Generic build stage non-zero → BUILD_ERROR
    if stage_key in ("build", "compile"):
        return FailureClassification(
            category=FailureCategory.BUILD_ERROR,
            severity=FailureSeverity.ERROR,
            retryable=False,
            confidence=0.55,
            message=(
                f"Build process exited with code {returncode} "
                f"(stage={stage_key!r}, tool={tool_label!r}) — no specific pattern matched."
            ),
            raw_signal=combined[:512] or None,
        )

    # Generic flash stage non-zero → FLASH_TIMEOUT (most common unexplained flash failure)
    if stage_key in ("flash", "program"):
        return FailureClassification(
            category=FailureCategory.FLASH_TIMEOUT,
            severity=FailureSeverity.ERROR,
            retryable=True,
            confidence=0.45,
            message=(
                f"Flash process exited with code {returncode} "
                f"(stage={stage_key!r}, tool={tool_label!r}) — no specific pattern matched."
            ),
            raw_signal=combined[:512] or None,
        )

    return FailureClassification(
        category=FailureCategory.UNKNOWN,
        severity=FailureSeverity.ERROR,
        retryable=False,
        confidence=0.20,
        message=(
            f"Non-zero exit {returncode} in stage={stage_key!r} "
            f"tool={tool_label!r}. No pattern matched; manual review needed."
        ),
        raw_signal=combined[:512] or None,
    )


# ---------------------------------------------------------------------------
# Batch helper (optional convenience — does NOT change architecture)
# ---------------------------------------------------------------------------

def classify_failures(
    signals: Sequence[dict],
) -> list[FailureClassification]:
    """
    Classify a sequence of failure signal dicts.

    Each dict accepts the same keyword arguments as classify_failure().
    Useful for post-mortem replay of logged failures.

    This is a thin wrapper — all classification logic remains in classify_failure().
    """
    return [classify_failure(**sig) for sig in signals]
