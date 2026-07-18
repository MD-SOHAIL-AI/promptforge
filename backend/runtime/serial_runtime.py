"""
runtime/serial_runtime.py
==========================
PromptForge AI — Serial Runtime

Owns exactly one thing: the serial port lifecycle.

ARCHITECTURAL CONTRACT (invariants that must never be violated):
  - MUST NOT call retry.py, engine.py, session.py, or failure_classifier.py
  - MUST NOT own orchestration logic
  - MUST NOT update session state
  - MUST NOT classify failures (raises raw exceptions; callers classify them)
  - MUST use bounded buffers (no unbounded memory growth)
  - MUST be asyncio-correct (no blocking calls on the event loop)
  - MUST be replay-safe (connect → disconnect → connect works cleanly)

DESIGN PHILOSOPHY:
  Every method either succeeds, raises a typed exception, or signals via
  SerialConnectionState. The caller (engine.py) decides what to do with that
  signal. This file never retries. It never classifies. It never orchestrates.

DEPENDENCY:
  pyserial >= 3.5  (standard embedded tooling dependency; no aioserial needed)
  All blocking pyserial calls are dispatched via asyncio.to_thread() so the
  event loop never stalls on a USB read.
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import enum
import logging
import time
import threading
from typing import AsyncIterator, Callable, Deque, Optional, Sequence

from .pyserial_compat import (
    PySerialUnavailableError,
    list_ports,
    require_pyserial,
    serial,
)

# ---------------------------------------------------------------------------
# Module logger — structured, lightweight, no external deps
# ---------------------------------------------------------------------------
logger = logging.getLogger("promptforge.serial")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SerialRuntimeError(Exception):
    """Base for all serial runtime errors. Never wraps retry/session logic."""


class SerialPortNotFoundError(SerialRuntimeError):
    """Raised when the requested port does not exist on the system."""


class SerialConnectionError(SerialRuntimeError):
    """Raised when a port exists but cannot be opened (busy, permissions, etc.)."""


class SerialTimeoutError(SerialRuntimeError):
    """Raised when a connection or read operation exceeds the configured timeout."""


class SerialRuntimeAlreadyRunningError(SerialRuntimeError):
    """Raised if monitor() is called while already monitoring."""


class SerialDependencyError(SerialRuntimeError):
    """Raised when pyserial is unavailable for a hardware operation."""


def _require_pyserial() -> None:
    try:
        require_pyserial(serial, list_ports)
    except PySerialUnavailableError as exc:
        raise SerialDependencyError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

@enum.unique
class SerialConnectionState(enum.Enum):
    """
    Lifecycle state machine for the serial port.

    Transitions:
      DISCONNECTED → CONNECTING   (connect() called)
      CONNECTING   → CONNECTED    (port opened successfully)
      CONNECTING   → FAILED       (port open failed, non-retryable)
      CONNECTED    → RECONNECTING (USB vanished during monitoring)
      RECONNECTING → CONNECTED    (port came back)
      RECONNECTING → FAILED       (reconnect budget exhausted)
      CONNECTED    → DISCONNECTED (disconnect() called)
      RECONNECTING → DISCONNECTED (disconnect() called mid-reconnect)
      *            → DISCONNECTED (reset() called)
    """
    DISCONNECTED  = "DISCONNECTED"
    CONNECTING    = "CONNECTING"
    CONNECTED     = "CONNECTED"
    RECONNECTING  = "RECONNECTING"
    FAILED        = "FAILED"


@enum.unique
class ObservationSource(enum.Enum):
    """Which subsystem produced this observation line."""
    DEVICE      = "DEVICE"       # Data read from the serial port
    RUNTIME     = "RUNTIME"      # Emitted by SerialRuntime itself (lifecycle events)
    OVERFLOW    = "OVERFLOW"     # Buffer overflow sentinel
    RECONNECT   = "RECONNECT"    # Reconnect lifecycle event


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class SerialObservation:
    """
    Immutable, structured observation emitted by the monitoring loop.

    Consumers iterate over these; they never receive raw bytes.

    Fields
    ------
    timestamp : Monotonic epoch seconds at the moment of line receipt.
    line      : Decoded text line (stripped of \\r\\n). May be empty string.
    source    : Which subsystem produced this observation.
    port      : The port name active when this observation was captured.
    metadata  : Optional key/value dict for source-specific extras.
                e.g. {"reconnect_attempt": 2} for RECONNECT source.
    """
    timestamp : float
    line      : str
    source    : ObservationSource
    port      : str
    metadata  : dict = dataclasses.field(default_factory=dict, compare=False)

    def is_device_line(self) -> bool:
        return self.source == ObservationSource.DEVICE

    def is_overflow(self) -> bool:
        return self.source == ObservationSource.OVERFLOW


@dataclasses.dataclass
class SerialConfig:
    """
    Configuration for a SerialRuntime instance.

    All limits are enforced at construction time so that runtime behaviour
    is fully determined before any I/O begins.

    Attributes
    ----------
    port           : Serial port path (e.g. "/dev/ttyUSB0", "COM3").
                     If None, the runtime will auto-detect the first available
                     port matching known_vid_pid_pairs.
    baudrate       : Bits per second. Defaults to 115200 (ESP32/Arduino default).
    read_timeout   : Seconds pyserial waits for a byte before returning None.
                     Keep low (0.1s) so the monitoring loop stays responsive
                     to cancellation signals.
    connect_timeout: Seconds to wait for the port to become available before
                     raising SerialTimeoutError.
    max_line_length: Lines longer than this are truncated and flagged.
                     Protects against devices emitting no newlines at all.
    buffer_maxlines: Maximum lines held in the observation ring buffer.
                     When full, the oldest line is silently dropped (deque
                     semantics). An OVERFLOW sentinel is injected.
    reconnect_delay : Seconds to wait between reconnect probes.
    max_reconnect_attempts: 0 means unlimited reconnect attempts.
    drain_on_connect: If True, flush pending bytes on open (clears boot ROM
                     garbage on ESP32 before monitoring begins).
    encoding       : Byte→string codec. "utf-8" with errors="replace" is safe
                     for all embedded targets.
    known_vid_pid_pairs: USB VID:PID pairs for auto-detection.
                     Defaults cover ESP32 (CP210x, CH340), STM32 (STLink),
                     Arduino (FTDI, CH340, ATmega16u2).
    """
    port                  : Optional[str] = None
    baudrate              : int           = 115_200
    read_timeout          : float         = 0.1      # seconds; low for cancel responsiveness
    connect_timeout       : float         = 10.0     # seconds
    max_line_length       : int           = 4_096    # bytes; truncate beyond
    buffer_maxlines       : int           = 2_000    # ring buffer capacity
    reconnect_delay       : float         = 1.5      # seconds between probes
    max_reconnect_attempts: int           = 10       # 0 = unlimited
    drain_on_connect      : bool          = True
    encoding              : str           = "utf-8"

    # USB VID:PID pairs for auto-detection (hex strings "VVVV:PPPP")
    known_vid_pid_pairs: tuple[str, ...] = dataclasses.field(default_factory=lambda: (
        "303a:1001",  # Espressif native USB Serial/JTAG
        "10c4:ea60",  # Silicon Labs CP2102 (ESP32 DevKit)
        "1a86:7523",  # CH340 (ESP32, Arduino Nano clones)
        "1a86:55d3",  # CH9102 (ESP32-C3 DevKit)
        "1a86:55d4",  # CH9102 (ESP32-S3 DevKit)
        "0403:6001",  # FTDI FT232R (Arduino Uno R3, many)
        "2341:0043",  # Arduino Uno ATmega16u2 DFU
        "2341:0001",  # Arduino Uno
        "0483:5740",  # STM32 Virtual COM Port
        "0483:374b",  # STLink-V2 (STM32 debug/serial)
        "1366:0105",  # SEGGER J-Link (STM32/ARM generic)
        "239a:cafe",  # Adafruit M0/M4 boards
    ))

    def __post_init__(self) -> None:
        if self.baudrate <= 0:
            raise ValueError(f"baudrate must be positive, got {self.baudrate}")
        if self.read_timeout <= 0:
            raise ValueError(f"read_timeout must be positive, got {self.read_timeout}")
        if self.buffer_maxlines < 10:
            raise ValueError(f"buffer_maxlines must be >= 10, got {self.buffer_maxlines}")
        if self.max_line_length < 64:
            raise ValueError(f"max_line_length must be >= 64, got {self.max_line_length}")
        if self.reconnect_delay < 0:
            raise ValueError(f"reconnect_delay must be >= 0, got {self.reconnect_delay}")
        if self.max_reconnect_attempts < 0:
            raise ValueError(
                f"max_reconnect_attempts must be >= 0, got {self.max_reconnect_attempts}"
            )


@dataclasses.dataclass
class SerialMetrics:
    """
    Lightweight runtime metrics. Read-only from outside SerialRuntime.

    Not a substitute for a real metrics system; just enough for debugging
    and test assertions without pulling in Prometheus or similar.
    """
    lines_received    : int   = 0
    bytes_received    : int   = 0
    overflow_events   : int   = 0
    reconnect_attempts: int   = 0
    decode_errors     : int   = 0
    lines_truncated   : int   = 0
    connect_count     : int   = 0

    def snapshot(self) -> dict:
        """Return a shallow copy as a plain dict (safe to log/serialize)."""
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Port auto-detection helper
# ---------------------------------------------------------------------------

def _detect_port(known_vid_pid_pairs: Sequence[str]) -> Optional[str]:
    """
    Scan connected USB serial ports and return the first match against the
    known VID:PID list.

    Returns None if nothing matches, allowing the caller to decide whether
    to wait or raise.

    This is a pure function with no side effects — safe to call repeatedly.
    """
    _require_pyserial()
    for port_info in list_ports.comports():
        if port_info.vid is None or port_info.pid is None:
            continue
        vid_pid = f"{port_info.vid:04x}:{port_info.pid:04x}"
        if vid_pid in known_vid_pid_pairs:
            logger.debug(
                "auto-detect: found port=%s vid_pid=%s desc=%s",
                port_info.device, vid_pid, port_info.description,
            )
            return port_info.device
    return None


# ---------------------------------------------------------------------------
# SerialRuntime
# ---------------------------------------------------------------------------

class SerialRuntime:
    """
    Serial port lifecycle manager for PromptForge AI.

    USAGE PATTERN (typical engine integration):

        config = SerialConfig(port="/dev/ttyUSB0", baudrate=115200)
        runtime = SerialRuntime(config)

        await runtime.connect()
        try:
            async for observation in runtime.monitor():
                # observation is a SerialObservation
                if observation.is_device_line():
                    process(observation.line)
        finally:
            await runtime.disconnect()

    THREADING MODEL:
        All blocking pyserial calls run in a thread pool via asyncio.to_thread().
        The event loop is never stalled. The monitoring loop polls for data
        every read_timeout seconds and yields control back to the event loop
        between reads so that cancellation is always responsive.

    STATE MACHINE:
        See SerialConnectionState docstring for the full transition table.
        State transitions are protected by an asyncio.Lock so that concurrent
        connect/disconnect calls are safe.
    """

    def __init__(self, config: SerialConfig) -> None:
        self._config  = config
        self._state   = SerialConnectionState.DISCONNECTED
        self._port    : Optional[serial.Serial] = None
        self._active_port_name: Optional[str]   = None

        # Observation ring buffer: bounded deque, O(1) append/drop
        self._buffer: Deque[SerialObservation] = collections.deque(
            maxlen=config.buffer_maxlines
        )

        # Runtime metrics — mutable, not thread-safe for writes (monitoring
        # loop is the only writer; reads from tests/logs are best-effort).
        self._metrics = SerialMetrics()

        # Lifecycle lock prevents concurrent connect/disconnect races
        self._lifecycle_lock = asyncio.Lock()

        # Monitoring task handle — tracked for clean cancellation
        self._monitor_task: Optional[asyncio.Task] = None

        # _stop_event is set to request a graceful monitoring shutdown
        # without using asyncio.CancelledError as a control flow mechanism.
        self._stop_event  = asyncio.Event()

        # Reconnect state
        self._reconnect_attempts = 0

        # Line accumulator for the streaming reader (handles partial reads)
        self._line_buffer = bytearray()

        logger.info(
            "SerialRuntime created port=%s baud=%d buffer=%d",
            config.port or "auto",
            config.baudrate,
            config.buffer_maxlines,
        )

    # ------------------------------------------------------------------
    # Public API — Lifecycle
    # ------------------------------------------------------------------

    @property
    def state(self) -> SerialConnectionState:
        return self._state

    @property
    def active_port(self) -> Optional[str]:
        """The port name currently open, or None if not connected."""
        return self._active_port_name

    @property
    def metrics(self) -> SerialMetrics:
        """Read-only access to runtime metrics (best-effort; not locked)."""
        return self._metrics

    async def connect(self) -> str:
        """
        Open the serial port and transition to CONNECTED.

        Returns the port name that was opened (useful when auto-detection
        was used and the caller needs to log or track the port).

        Raises
        ------
        SerialPortNotFoundError   : Port not found within connect_timeout.
        SerialConnectionError     : Port found but could not be opened.
        SerialTimeoutError        : connect_timeout expired without success.
        SerialRuntimeAlreadyRunningError: Already in CONNECTED state.
        """
        _require_pyserial()
        async with self._lifecycle_lock:
            if self._state == SerialConnectionState.CONNECTED:
                raise SerialRuntimeAlreadyRunningError(
                    f"Already connected to {self._active_port_name}"
                )
            self._set_state(SerialConnectionState.CONNECTING)
            port_name = await self._open_port_with_timeout()
            self._metrics.connect_count += 1
            self._set_state(SerialConnectionState.CONNECTED)
            self._stop_event.clear()
            logger.info("connected port=%s baud=%d", port_name, self._config.baudrate)
            return port_name

    async def disconnect(self) -> None:
        """
        Close the serial port gracefully.

        Safe to call in any state, including FAILED or DISCONNECTED.
        After this returns, the runtime is in DISCONNECTED state and can
        be connected again (replay-safe).
        """
        async with self._lifecycle_lock:
            # Signal the monitoring loop to stop before we close the port.
            # This prevents the reader thread from trying to read a closed fd.
            self._stop_event.set()

            if self._monitor_task is not None and not self._monitor_task.done():
                self._monitor_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(self._monitor_task), timeout=3.0
                    )
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                self._monitor_task = None

            await self._close_port()
            self._set_state(SerialConnectionState.DISCONNECTED)
            logger.info("disconnected port=%s", self._active_port_name or "none")
            self._active_port_name = None

    async def reset(self) -> None:
        """
        Force-disconnect and clear all internal state.

        Use when you want a clean slate before re-connecting after a FAILED
        state. Equivalent to disconnect() + clearing metrics and buffer.
        """
        await self.disconnect()
        self._buffer.clear()
        self._line_buffer.clear()
        self._reconnect_attempts = 0
        self._metrics = SerialMetrics()
        logger.info("SerialRuntime reset complete")

    # ------------------------------------------------------------------
    # Public API - Raw byte I/O
    # ------------------------------------------------------------------

    async def read(self, size: int = 1) -> bytes:
        """Read up to ``size`` bytes from the connected serial port.

        The configured ``read_timeout`` bounds the blocking pyserial call.
        Raw reads are intentionally unavailable while ``monitor()`` owns the
        receive stream, preventing two consumers from racing for bytes.
        """
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("size must be a positive integer")
        self._require_raw_io_available()
        return await asyncio.to_thread(self._read_bytes_sync, size)

    async def write(self, data: bytes) -> int:
        """Write bytes to the connected serial port and return bytes written."""
        if not isinstance(data, bytes):
            raise ValueError("data must be bytes")
        if not data:
            raise ValueError("data must be non-empty")
        self._require_connected()
        return await asyncio.to_thread(self._write_bytes_sync, data)

    async def read_until(
        self,
        expected: bytes = b"\n",
        size: Optional[int] = None,
    ) -> bytes:
        """Read through ``expected`` or until the configured timeout/limit.

        ``size`` defaults to ``max_line_length`` so a device that never emits
        the delimiter cannot grow memory without bound.
        """
        if not isinstance(expected, bytes) or not expected:
            raise ValueError("expected must be non-empty bytes")
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size <= 0
        ):
            raise ValueError("size must be a positive integer or None")
        self._require_raw_io_available()
        limit = self._config.max_line_length if size is None else size
        return await asyncio.to_thread(
            self._read_until_sync,
            expected,
            limit,
        )

    # ------------------------------------------------------------------
    # Public API — Monitoring
    # ------------------------------------------------------------------

    async def monitor(self) -> AsyncIterator[SerialObservation]:
        """
        Async generator that yields SerialObservation objects.

        Designed to be consumed with:

            async for obs in runtime.monitor():
                handle(obs)

        CANCELLATION:
            The generator respects asyncio cancellation. When the outer task
            is cancelled (or disconnect() is called), the generator exits
            cleanly within one read_timeout window. No cleanup burden on
            the caller.

        RECONNECT:
            If the USB device disappears (OSError on read), the loop
            transitions to RECONNECTING and attempts to re-open the port.
            RECONNECT observations are yielded so the caller can observe the
            lifecycle without needing to inspect internal state.

        OVERFLOW:
            When the ring buffer fills, the oldest observation is dropped
            (deque semantics) and an OVERFLOW sentinel is yielded. This
            prevents unbounded memory growth from serial floods. The caller
            is notified so it can decide to log/escalate.

        PARTIAL READS:
            pyserial readline() can return partial lines if the device
            resets mid-write. The runtime accumulates bytes internally and
            only yields complete newline-terminated lines. Partial lines at
            the end of a session are yielded as-is on disconnect.

        MALFORMED UTF-8:
            Decoded with errors="replace" (U+FFFD substitution). The metrics
            counter decode_errors is incremented for each replacement so
            callers can detect baud-rate mismatches without crashing.
        """
        if self._state != SerialConnectionState.CONNECTED:
            raise SerialRuntimeError(
                f"monitor() requires CONNECTED state, got {self._state.value}"
            )
        if self._monitor_task is not None and not self._monitor_task.done():
            raise SerialRuntimeAlreadyRunningError(
                "monitor() is already running in another task"
            )

        # Register this coroutine's task so disconnect() can cancel it
        self._monitor_task = asyncio.current_task()

        # Yield a lifecycle observation so consumers can log the session start
        yield self._make_runtime_obs("monitoring started", {"port": self._active_port_name})

        try:
            async for obs in self._monitoring_loop():
                yield obs
        except asyncio.CancelledError:
            logger.debug("monitor() task cancelled")
            raise
        finally:
            # Drain any partial line accumulated in the byte buffer
            if self._line_buffer:
                partial = self._flush_line_buffer()
                if partial.strip():
                    yield self._make_obs(partial)
            self._monitor_task = None
            yield self._make_runtime_obs("monitoring ended", {"port": self._active_port_name})

    # ------------------------------------------------------------------
    # Public API — Introspection
    # ------------------------------------------------------------------

    def drain_buffer(self) -> list[SerialObservation]:
        """
        Return all buffered observations and clear the buffer.

        Useful for test assertions or post-mortem analysis. Not intended
        for use during live monitoring (use the async generator instead).
        """
        obs = list(self._buffer)
        self._buffer.clear()
        return obs

    def peek_buffer(self) -> list[SerialObservation]:
        """Return buffered observations without clearing (snapshot copy)."""
        return list(self._buffer)

    @staticmethod
    def list_available_ports() -> list[dict]:
        """
        Return a list of currently connected serial ports with metadata.

        Each dict contains: port, description, vid, pid, serial_number.
        Pure utility — no state side effects.
        """
        _require_pyserial()
        ports = []
        for p in list_ports.comports():
            ports.append({
                "port":          p.device,
                "description":   p.description,
                "vid":           f"{p.vid:04x}" if p.vid else None,
                "pid":           f"{p.pid:04x}" if p.pid else None,
                "serial_number": p.serial_number,
            })
        return ports

    # ------------------------------------------------------------------
    # Internal — Port open/close
    # ------------------------------------------------------------------

    async def _open_port_with_timeout(self) -> str:
        """
        Resolve and open the serial port within connect_timeout.

        If config.port is None, auto-detection is attempted on each probe
        cycle. This handles the common case where the user hasn't specified
        a port but the device is plugged in.

        Returns the actual port name opened.
        """
        deadline = time.monotonic() + self._config.connect_timeout
        probe_interval = 0.5  # seconds between availability probes

        while True:
            port_name = self._config.port or _detect_port(self._config.known_vid_pid_pairs)

            if port_name is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SerialTimeoutError(
                        f"No known serial device detected within "
                        f"{self._config.connect_timeout}s"
                    )
                logger.debug("auto-detect: no device yet, retrying in %.1fs", probe_interval)
                await asyncio.sleep(min(probe_interval, remaining))
                continue

            try:
                await asyncio.to_thread(self._open_port_sync, port_name)
                return port_name
            except serial.SerialException as exc:
                msg = str(exc).lower()
                # Port exists but is busy or permission-denied → give up immediately
                if "permission" in msg or "access denied" in msg:
                    raise SerialConnectionError(
                        f"Permission denied on {port_name}: {exc}"
                    ) from exc
                if "busy" in msg or "resource temporarily" in msg:
                    raise SerialConnectionError(
                        f"Port {port_name} is busy: {exc}"
                    ) from exc
                # Port doesn't exist yet (device still enumerating) → wait and retry
                if time.monotonic() >= deadline:
                    raise SerialTimeoutError(
                        f"Could not open {port_name} within {self._config.connect_timeout}s: {exc}"
                    ) from exc
                logger.debug("port %s not ready yet (%s), retrying", port_name, exc)
                await asyncio.sleep(min(probe_interval, deadline - time.monotonic()))

    def _open_port_sync(self, port_name: str) -> None:
        """
        Blocking pyserial open — MUST only be called via asyncio.to_thread().

        Configures the port fully before returning so the caller never sees
        a half-initialised serial object.
        """
        try:
            s = serial.Serial(
                port=port_name,
                baudrate=self._config.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._config.read_timeout,
                write_timeout=2.0,
                exclusive=True,        # Prevent other processes from stealing the port
            )
        except serial.SerialException:
            raise  # caller handles

        if self._config.drain_on_connect:
            try:
                s.reset_input_buffer()   # flush boot ROM garbage (ESP32 / STM32)
                s.reset_output_buffer()
            except serial.SerialException:
                pass  # non-fatal; port may not support flush

        # Store atomically; other threads only read _port after CONNECTED
        self._port = s
        self._active_port_name = port_name

    async def _close_port(self) -> None:
        """Close the port in a thread; safe to call if port is already None."""
        port = self._port
        self._port = None
        if port is not None:
            try:
                await asyncio.to_thread(port.close)
            except Exception as exc:
                logger.debug("error closing port (ignored): %s", exc)

    # ------------------------------------------------------------------
    # Internal — Monitoring loop
    # ------------------------------------------------------------------

    async def _monitoring_loop(self) -> AsyncIterator[SerialObservation]:
        """
        Core read loop. Yields observations until stopped or failed.

        DESIGN: We use asyncio.to_thread for the blocking readline() call
        instead of a busy-poll because:
          1. It keeps the event loop free for other tasks.
          2. The thread is released between reads (not pinned).
          3. read_timeout controls the maximum latency to cancellation response.

        OVERFLOW PROTECTION: The ring buffer (deque with maxlen) automatically
        drops the oldest entry. We detect the drop by checking buffer length
        before and after append — if it didn't grow, something was dropped.
        We then inject an OVERFLOW sentinel so the consumer knows.

        RECONNECT: On OSError we yield a RECONNECT observation and enter the
        reconnect sub-loop. If reconnect succeeds, monitoring resumes on the
        new port. If the reconnect budget is exhausted, we raise to signal
        FAILED state to the outer connect() caller.
        """
        consecutive_empty_reads = 0
        # Threshold after which we log a warning about a potentially dead port
        _EMPTY_READ_WARN_THRESHOLD = int(30 / self._config.read_timeout)

        while not self._stop_event.is_set():
            # --- Read one chunk from the port ---
            try:
                raw_bytes = await asyncio.to_thread(self._read_line_sync)
            except (OSError, serial.SerialException) as exc:
                if self._stop_event.is_set():
                    return  # clean shutdown in progress
                logger.warning("serial read error: %s — entering reconnect", exc)
                self._metrics.reconnect_attempts += 1
                async for obs in self._reconnect_loop(exc):
                    yield obs
                if self._state != SerialConnectionState.CONNECTED:
                    return  # reconnect exhausted; caller sees FAILED state
                continue
            except asyncio.CancelledError:
                return

            # --- Empty read (timeout, no data) ---
            if not raw_bytes:
                consecutive_empty_reads += 1
                if consecutive_empty_reads == _EMPTY_READ_WARN_THRESHOLD:
                    logger.debug(
                        "port=%s no data for ~30s (normal for quiet devices)",
                        self._active_port_name,
                    )
                await asyncio.sleep(0)  # yield control; essential for cancellation
                continue

            consecutive_empty_reads = 0

            # --- Decode ---
            prior_errors = self._metrics.decode_errors
            lines = self._decode_and_split(raw_bytes)
            if self._metrics.decode_errors > prior_errors:
                logger.debug(
                    "utf-8 decode replacement on port=%s (baud mismatch?)",
                    self._active_port_name,
                )

            # --- Yield each complete line ---
            for line in lines:
                obs = self._make_obs(line)
                was_full = len(self._buffer) == self._buffer.maxlen
                self._buffer.append(obs)

                if was_full:
                    # deque dropped the oldest — notify consumer
                    self._metrics.overflow_events += 1
                    overflow_obs = SerialObservation(
                        timestamp=time.monotonic(),
                        line="[OVERFLOW: oldest observation dropped from ring buffer]",
                        source=ObservationSource.OVERFLOW,
                        port=self._active_port_name or "",
                        metadata={"buffer_maxlines": self._config.buffer_maxlines},
                    )
                    yield overflow_obs

                yield obs

    def _read_line_sync(self) -> bytes:
        """
        Blocking readline from the serial port.

        MUST only be called via asyncio.to_thread().

        Returns empty bytes on timeout (normal; means no data yet).
        Raises OSError / SerialException on hardware errors.

        LINE LENGTH LIMIT: pyserial's readline() accumulates until \\n.
        On a device doing Serial.print("x") in an infinite loop with no
        newline, this grows forever. We guard against this with max_line_length
        by using read() with a size limit instead of readline() when the
        accumulator exceeds the threshold.

        The approach: read up to max_line_length bytes. If the chunk contains
        a newline, readline semantics are preserved. If not, we force-truncate
        and mark it. This is safe for both well-behaved and pathological devices.
        """
        port = self._port
        if port is None or not port.is_open:
            raise serial.SerialException("port is closed")

        # Use read() with size limit rather than readline() to prevent
        # unbounded accumulation on devices with no newlines.
        # The line-assembly logic lives in _decode_and_split().
        try:
            chunk = port.read(self._config.max_line_length)
        except serial.SerialException:
            raise
        return chunk

    def _require_connected(self) -> None:
        port = self._port
        if (
            self._state != SerialConnectionState.CONNECTED
            or port is None
            or not port.is_open
        ):
            raise SerialConnectionError("serial port is not connected")

    def _require_raw_io_available(self) -> None:
        self._require_connected()
        if self._monitor_task is not None and not self._monitor_task.done():
            raise SerialRuntimeAlreadyRunningError(
                "raw reads are unavailable while monitor() is running"
            )

    def _read_bytes_sync(self, size: int) -> bytes:
        self._require_connected()
        assert self._port is not None
        return self._port.read(size)

    def _write_bytes_sync(self, data: bytes) -> int:
        self._require_connected()
        assert self._port is not None
        written = self._port.write(data)
        self._port.flush()
        return int(written)

    def _read_until_sync(self, expected: bytes, size: int) -> bytes:
        self._require_connected()
        assert self._port is not None
        return self._port.read_until(expected=expected, size=size)

    def _decode_and_split(self, raw: bytes) -> list[str]:
        """
        Decode raw bytes and split into complete lines.

        Accumulates partial lines in self._line_buffer across calls.
        Only yields complete newline-terminated strings. The final partial
        line (if any) is held in the buffer until the next read or flush.

        MALFORMED UTF-8: errors="replace" substitutes U+FFFD and increments
        decode_errors so callers can detect baud-rate mismatches without
        crashing. This is the correct embedded default — devices routinely
        emit garbage bytes on power-on before the UART stabilises.

        CRLF: strips both \\r and \\n so Windows-style line endings from
        STM32 HAL printf() and Arduino Serial.println() are handled uniformly.

        LINE TRUNCATION: Lines longer than max_line_length are truncated and
        tagged with a [TRUNCATED] suffix. This protects the ring buffer from
        a single huge observation blowing memory budget.
        """
        self._metrics.bytes_received += len(raw)

        # Count replacement characters as decode errors
        decoded = raw.decode(self._config.encoding, errors="replace")
        self._metrics.decode_errors += decoded.count("\ufffd")

        self._line_buffer.extend(raw)

        lines = []
        while True:
            # Find next newline in the accumulated buffer
            nl_pos = -1
            for i, b in enumerate(self._line_buffer):
                if b in (ord('\n'), ord('\r')):
                    nl_pos = i
                    break
            if nl_pos == -1:
                # No complete line yet
                # Guard against a device that never sends a newline
                if len(self._line_buffer) >= self._config.max_line_length:
                    chunk = bytes(self._line_buffer[:self._config.max_line_length])
                    del self._line_buffer[:self._config.max_line_length]
                    line = chunk.decode(self._config.encoding, errors="replace")
                    self._metrics.lines_truncated += 1
                    lines.append(line + " [TRUNCATED]")
                break

            # Extract up to the newline
            line_bytes = bytes(self._line_buffer[:nl_pos])
            # Consume newline and any immediately following \r or \n (CRLF pair)
            skip = nl_pos + 1
            while skip < len(self._line_buffer) and self._line_buffer[skip] in (ord('\r'), ord('\n')):
                skip += 1
            del self._line_buffer[:skip]

            if len(line_bytes) > self._config.max_line_length:
                line_bytes = line_bytes[:self._config.max_line_length]
                self._metrics.lines_truncated += 1
                line = line_bytes.decode(self._config.encoding, errors="replace") + " [TRUNCATED]"
            else:
                line = line_bytes.decode(self._config.encoding, errors="replace")

            self._metrics.lines_received += 1
            lines.append(line)

        return lines

    def _flush_line_buffer(self) -> str:
        """
        Drain any partial line at session end.
        Returns the partial content and clears the buffer.
        """
        if not self._line_buffer:
            return ""
        partial = bytes(self._line_buffer).decode(self._config.encoding, errors="replace")
        self._line_buffer.clear()
        return partial

    # ------------------------------------------------------------------
    # Internal — Reconnect loop
    # ------------------------------------------------------------------

    async def _reconnect_loop(
        self,
        trigger_exc: Exception,
    ) -> AsyncIterator[SerialObservation]:
        """
        Attempt to re-open the serial port after a hardware disconnect.

        DESIGN DECISIONS:

        1. PORT CHANGE AFTER RECONNECT
           ESP32 and STM32 devices occasionally re-enumerate on a different
           /dev/ttyUSBN index after a USB reset. We handle this by:
           a) First probing the last-known port name.
           b) If that fails, falling back to VID:PID auto-detection.
           c) Updating _active_port_name to the new port.
           This means consumers observing the RECONNECT source see the old
           AND new port names in the metadata.

        2. RECONNECT BUDGET
           max_reconnect_attempts > 0 limits the reconnect loop. When
           exhausted, state is set to FAILED and we return without raising —
           the monitoring loop caller sees the state change and stops.

        3. RAPID RECONNECT LOOPS
           A device that reboots in a tight loop (e.g. watchdog cascade) would
           saturate reconnect attempts quickly. The reconnect_delay is not
           shortened between attempts, which provides natural back-pressure.

        4. STOP DURING RECONNECT
           We check _stop_event on every iteration so that disconnect() called
           from another task is honoured within one reconnect_delay window.
        """
        self._set_state(SerialConnectionState.RECONNECTING)
        old_port = self._active_port_name

        yield SerialObservation(
            timestamp=time.monotonic(),
            line=f"USB disconnect detected on {old_port}: {trigger_exc}",
            source=ObservationSource.RECONNECT,
            port=old_port or "",
            metadata={"event": "disconnect", "error": str(trigger_exc)},
        )

        # Close the now-dead port handle without waiting for the lock
        # (we're already inside the monitoring context)
        await self._close_port_unsafe()

        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            self._reconnect_attempts += 1

            if (self._config.max_reconnect_attempts > 0
                    and attempt > self._config.max_reconnect_attempts):
                logger.error(
                    "reconnect budget exhausted after %d attempts on port=%s",
                    attempt - 1,
                    old_port,
                )
                self._set_state(SerialConnectionState.FAILED)
                yield SerialObservation(
                    timestamp=time.monotonic(),
                    line=f"Reconnect failed: budget exhausted ({attempt-1} attempts)",
                    source=ObservationSource.RECONNECT,
                    port=old_port or "",
                    metadata={"event": "failed", "attempts": attempt - 1},
                )
                return

            logger.info(
                "reconnect attempt %d/%s for port=%s",
                attempt,
                self._config.max_reconnect_attempts or "∞",
                old_port,
            )

            await asyncio.sleep(self._config.reconnect_delay)

            if self._stop_event.is_set():
                return

            # Probe strategy: try last-known port first, then auto-detect
            candidate = old_port or _detect_port(self._config.known_vid_pid_pairs)
            if candidate is None:
                candidate = _detect_port(self._config.known_vid_pid_pairs)

            if candidate is None:
                logger.debug("reconnect probe %d: no device visible yet", attempt)
                continue

            try:
                await asyncio.to_thread(self._open_port_sync, candidate)
            except (serial.SerialException, OSError) as exc:
                logger.debug("reconnect probe %d: %s still not ready: %s", attempt, candidate, exc)
                continue

            # Success
            new_port = self._active_port_name
            self._set_state(SerialConnectionState.CONNECTED)
            self._reconnect_attempts += 1
            logger.info(
                "reconnected on port=%s (was %s) after %d attempt(s)",
                new_port, old_port, attempt,
            )
            yield SerialObservation(
                timestamp=time.monotonic(),
                line=f"Reconnected on {new_port} (was {old_port}) after {attempt} attempt(s)",
                source=ObservationSource.RECONNECT,
                port=new_port or "",
                metadata={
                    "event":        "reconnected",
                    "attempts":     attempt,
                    "old_port":     old_port,
                    "new_port":     new_port,
                },
            )
            return

    async def _close_port_unsafe(self) -> None:
        """
        Close the port handle without acquiring the lifecycle lock.

        Used only from inside _reconnect_loop(), which is already running
        inside the monitoring loop (lock not held there).
        """
        port = self._port
        self._port = None
        if port is not None:
            try:
                await asyncio.to_thread(port.close)
            except Exception:
                pass  # Dead port; close is best-effort

    # ------------------------------------------------------------------
    # Internal — State management
    # ------------------------------------------------------------------

    def _set_state(self, new_state: SerialConnectionState) -> None:
        old = self._state
        self._state = new_state
        logger.debug("state: %s → %s", old.value, new_state.value)

    # ------------------------------------------------------------------
    # Internal — Observation factory helpers
    # ------------------------------------------------------------------

    def _make_obs(self, line: str) -> SerialObservation:
        return SerialObservation(
            timestamp=time.monotonic(),
            line=line,
            source=ObservationSource.DEVICE,
            port=self._active_port_name or "",
        )

    def _make_runtime_obs(self, message: str, meta: dict | None = None) -> SerialObservation:
        return SerialObservation(
            timestamp=time.monotonic(),
            line=message,
            source=ObservationSource.RUNTIME,
            port=self._active_port_name or "",
            metadata=meta or {},
        )

    # ------------------------------------------------------------------
    # Async context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "SerialRuntime":
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()

    # ------------------------------------------------------------------
    # repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"SerialRuntime(port={self._active_port_name!r}, "
            f"state={self._state.value}, "
            f"baud={self._config.baudrate})"
        )
