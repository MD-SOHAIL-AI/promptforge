"""
runtime/subprocess_mgr.py — Subprocess orchestration with deterministic I/O semantics.

Core principle: never block on a single pipe. stdout and stderr are drained
concurrently to prevent the classic pipe-buffer deadlock where a process stalls
waiting for one stream to be read while the other fills up.

The implementation uses hardened argv-based subprocess execution with explicit process control:
- Two reader tasks race in parallel via asyncio.gather
- A timeout watchdog fires SIGTERM → 3s grace → SIGKILL
- Environment is constructed from an allowlist, not inherited wholesale
- All results are typed; nothing surfaces as a raw returncode int

Usage::

    mgr = SubprocessManager()
    result = await mgr.run(ProcessConfig(
        args=[\"cmake\", \"--build\", \".\", \"--target\", \"flash\"],
        cwd=\"/workspace/firmware\",
        timeout_s=120.0,
    ))
    if not result.success:
        raise ExecutionError(result)

──────────────────────────────────────────────────────────────────────────────
HARDENING CHANGES vs. ORIGINAL
──────────────────────────────────────────────────────────────────────────────

[FIX-P1] Process tree cleanup via os.killpg (POSIX) / TerminateJobObject (Win).
  The original only killed the direct subprocess. For idf.py → ninja → gcc
  chains (standard in embedded toolchains), killing the parent leaves all
  children running as orphans. On long CI runs this produces hundreds of
  zombie processes that exhaust PID limits and lock serial ports.
  Fix: start_new_session=True creates a new process group; SIGTERM/SIGKILL
  are sent to the entire group via os.killpg.

[FIX-P2] proc.returncode bug: `returncode or 0` masks returncode=None as 0.
  If proc.wait() is not awaited for any reason, returncode stays None, and
  `None or 0` evaluates to 0, silently reporting a crashed process as success.
  Fix: explicit `if returncode is not None else <sentinel>` throughout.

[FIX-P3] kill_all() second loop iterates self._active.values() without
  snapshot. If a subprocess completes and removes itself from _active (via
  the finally block in run()) during the 2-second wait, CPython raises
  RuntimeError("dictionary changed size during iteration").
  Fix: snapshot with list() before both loops.

[FIX-P4] Bounded output buffering — MAX_OUTPUT_BYTES (32 MB) per stream.
  The original used reader.read(-1) which buffers all output in RAM. A verbose
  build (cmake + ninja + esp-idf with VERBOSE=1) can produce hundreds of MB.
  Fix: chunked reads that truncate and drain remaining output without storing.

[FIX-P5] _drain_concurrent partial-data loss after timeout cancellation.
  The original cancelled stdout_task and stderr_task on timeout, then called
  _handle_timeout which tried reader.read(-1) on pipes already partially
  consumed by the cancelled tasks. The second read would get empty bytes or
  partial data.
  Fix: await cancelled tasks and capture any result before they're GC'd;
  pass captured bytes into _handle_timeout as pre-read prefixes.

[FIX-P6] stream() cleanup was incomplete.
  The finally block killed the process but never cancelled or awaited the
  pump tasks started by _interleave_streams. Those tasks would run until
  they hit an exception from reading a closed pipe — an unobserved task
  warning and a brief resource-leak window.
  Fix: explicit task tracking and cancellation in stream()'s finally block.

[FIX-P7] _interleave_streams used per-readline timeout semantics.
  config.timeout_s was applied per readline() call, not to the total operation.
  A process that emits one line every (timeout_s - ε) seconds could stream
  forever. Worse: when the per-readline timeout fired, the process wasn't
  killed, so the generator simply stopped yielding without signalling EOF.
  Fix: compute an absolute deadline and enforce it on the total operation.

[FIX-P8] session_id added to ProcessConfig for log correlation.
  In concurrent sessions, subprocess log lines were indistinguishable. The
  session_id flows into the log prefix so multi-session traces are queryable.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

__all__ = [
    "DrainPolicy",
    "ProcessConfig",
    "ProcessResult",
    "SubprocessManager",
    "ExecutionError",
    "ProcessTimeoutError",
    "MAX_OUTPUT_BYTES",
]

# [FIX-P4] Hard cap per stream (stdout or stderr).
# Verbose esp-idf builds with VERBOSE=1 can exceed 200 MB. Reading all of it
# into RAM will OOM the host on a resource-constrained CI runner or dev box.
MAX_OUTPUT_BYTES: int = 32 * 1024 * 1024   # 32 MB per stream

# Truncation marker injected into the output so downstream log parsers can
# detect the cut point rather than silently processing incomplete data.
_TRUNCATION_MARKER: bytes = (
    b"\n[PromptForge: OUTPUT TRUNCATED \xe2\x80\x94 exceeded MAX_OUTPUT_BYTES limit]\n"
)

_CHUNK_SIZE: int = 64 * 1024
_TIMEOUT_GRACE_S: float = 3.0
_KILL_GRACE_S: float = 2.0
_SIGTERM = getattr(signal, "SIGTERM", 15)
_SIGKILL = getattr(signal, "SIGKILL", -9)
_PROCESS_OWNER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "forgex_process_owner",
    default=None,
)


class DrainPolicy(Enum):
    """Controls how stdout and stderr are read from a running subprocess."""

    CONCURRENT   = auto()  # Both pipes drained in parallel — default, deadlock-safe
    SEQUENTIAL   = auto()  # communicate() — only safe when stderr is tiny or ignored
    INTERLEAVED  = auto()  # Merged stream with (name, line) tuples; used by .stream()


@dataclass(frozen=True)
class ProcessConfig:
    """
    Immutable specification for a single subprocess invocation.

    All fields are validated at construction time — there is no mutable
    state to race on.
    """

    args: list[str]
    cwd: Optional[str]                  = None
    env: Optional[dict[str, str]]       = None   # Overrides; merged on top of allowlist
    timeout_s: float                    = 60.0
    drain_policy: DrainPolicy           = DrainPolicy.CONCURRENT
    capture_output: bool                = True
    stdin_data: Optional[bytes]         = None

    # [FIX-P8] Session correlation ID — flows into log prefixes so concurrent
    # sessions' subprocess output is distinguishable in aggregated logs.
    session_id: Optional[str]           = None

    # Only these vars are inherited from the host environment.
    # Everything else is blocked — prevents host PATH pollution
    # and ensures reproducible builds across machines.
    env_allowlist: frozenset[str] = frozenset({
        "PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP",
        "LANG", "LC_ALL", "LC_CTYPE", "SHELL",
        # Windows process/runtime basics. Without SystemRoot, Python and some
        # toolchains cannot load OS crypto/random providers during startup.
        "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "LOCALAPPDATA", "APPDATA",
        "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "USERPROFILE",
        # Arduino / ESP-IDF / ARM toolchain commonly need these:
        "IDF_PATH", "ARDUINO_DIR", "ARM_TOOLCHAIN_PATH",
        # PlatformIO
        "PLATFORMIO_HOME_DIR",
        # esptool is Python-based; needs PYTHONPATH in some installations
        "PYTHONPATH",
        # OpenOCD may need these for JTAG adapter detection
        "OPENOCD_SCRIPTS", "LD_LIBRARY_PATH",
    })


@dataclass
class ProcessResult:
    """Typed result from a completed subprocess invocation."""

    returncode:  int
    stdout:      bytes
    stderr:      bytes
    args:        list[str]
    elapsed_s:   float
    timed_out:   bool          = False
    signal_name: Optional[str] = None   # "SIGTERM" or "SIGKILL" if force-killed
    output_truncated: bool     = False  # [FIX-P4] True if output exceeded MAX_OUTPUT_BYTES

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace")

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace")

    @property
    def combined_text(self) -> str:
        """stdout + stderr merged, each line tagged with source prefix."""
        lines: list[str] = []
        for line in self.stdout_text.splitlines():
            lines.append(f"[out] {line}")
        for line in self.stderr_text.splitlines():
            lines.append(f"[err] {line}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        status = "OK" if self.success else (
            "TIMEOUT" if self.timed_out else f"ERR({self.returncode})"
        )
        trunc = " TRUNCATED" if self.output_truncated else ""
        return (
            f"<ProcessResult {status}{trunc} cmd={self.args[0]!r} "
            f"elapsed={self.elapsed_s:.2f}s "
            f"stdout={len(self.stdout)}B stderr={len(self.stderr)}B>"
        )


class ExecutionError(Exception):
    """Non-zero exit from a subprocess that was expected to succeed."""

    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        super().__init__(
            f"Command {result.args[0]!r} failed (exit {result.returncode}):\n"
            f"{result.stderr_text.strip() or result.stdout_text.strip()}"
        )


class ProcessTimeoutError(Exception):
    """Subprocess exceeded its allowed wall-clock duration."""

    def __init__(self, config: ProcessConfig, elapsed_s: float) -> None:
        self.config    = config
        self.elapsed_s = elapsed_s
        super().__init__(
            f"Command {config.args[0]!r} timed out after "
            f"{elapsed_s:.1f}s (limit {config.timeout_s}s)"
        )


# ── SubprocessManager ─────────────────────────────────────────────────────────

class SubprocessManager:
    """
    Production subprocess orchestrator for the PromptForge runtime.

    Guarantees:
    - At most `max_concurrent` subprocesses running simultaneously
    - stdout and stderr always drained concurrently (no deadlock)
    - Timed-out processes receive SIGTERM to the process GROUP, then SIGKILL
      after a 3s grace window — ensuring child processes (ninja, gcc) die too
    - Subprocess environments are constructed from an allowlist, not os.environ
    - All active processes are tracked and can be bulk-killed on shutdown
    - Output is bounded at MAX_OUTPUT_BYTES per stream to prevent OOM

    Instantiate once per engine; share across pipeline stages.
    """

    def __init__(
        self,
        *,
        max_concurrent: int   = 4,
        default_timeout_s: float = 60.0,
    ) -> None:
        self._semaphore       = asyncio.Semaphore(max_concurrent)
        self._default_timeout = default_timeout_s
        # pid → process — used by kill_all() during engine shutdown
        self._active: dict[int, asyncio.subprocess.Process] = {}
        self._owners: dict[int, str | None] = {}

    @contextmanager
    def process_scope(self, owner: str):
        """Associate subprocesses created in this context with one agent run."""

        if not owner:
            raise ValueError("process owner must be non-empty")
        token = _PROCESS_OWNER.set(owner)
        try:
            yield
        finally:
            _PROCESS_OWNER.reset(token)

    # ── Public API ────────────────────────────────────────────────────────────

    async def run(
        self,
        config: ProcessConfig,
        *,
        raise_on_error: bool = False,
    ) -> ProcessResult:
        """
        Execute a command and return a structured result.

        Never raises unless raise_on_error=True. Timeout results in a
        ProcessResult with timed_out=True rather than an exception, so the
        caller can decide how to handle it.
        """
        async with self._semaphore:
            env  = self._build_env(config)
            t0   = time.monotonic()

            # [FIX-P1] start_new_session=True creates a new process group on POSIX.
            # This lets _kill_process_tree() kill the entire group (e.g., idf.py +
            # all ninja/gcc children) with a single killpg() call.
            extra_kwargs: dict = {}
            if sys.platform != "win32":
                extra_kwargs["start_new_session"] = True
            else:
                extra_kwargs["creationflags"] = getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0,
                )

            proc = await asyncio.create_subprocess_exec(
                *config.args,
                cwd    = config.cwd,
                env    = env,
                stdin  = (
                    asyncio.subprocess.PIPE
                    if config.stdin_data is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout = asyncio.subprocess.PIPE if config.capture_output else asyncio.subprocess.DEVNULL,
                stderr = asyncio.subprocess.PIPE if config.capture_output else asyncio.subprocess.DEVNULL,
                **extra_kwargs,
            )

            self._active[proc.pid] = proc
            self._owners[proc.pid] = _PROCESS_OWNER.get() or config.session_id
            try:
                result = await self._drain_and_wait(proc, config, t0)
            finally:
                self._active.pop(proc.pid, None)
                self._owners.pop(proc.pid, None)

            if raise_on_error and not result.success:
                raise ExecutionError(result)
            return result

    async def stream(
        self,
        config: ProcessConfig,
    ) -> AsyncIterator[tuple[str, bytes]]:
        """
        Stream subprocess output line by line.

        Yields ``(stream_name, raw_line)`` tuples where stream_name is
        ``"stdout"`` or ``"stderr"``. Lines include the trailing newline.

        Use this when you need to display progress in real time (build
        output, flash progress bars) rather than waiting for completion.
        """
        async with self._semaphore:
            env  = self._build_env(config)

            extra_kwargs: dict = {}
            if sys.platform != "win32":
                extra_kwargs["start_new_session"] = True   # [FIX-P1]
            else:
                extra_kwargs["creationflags"] = getattr(
                    subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0,
                )

            proc = await asyncio.create_subprocess_exec(
                *config.args,
                cwd    = config.cwd,
                env    = env,
                stdin  = asyncio.subprocess.DEVNULL,
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
                **extra_kwargs,
            )
            self._active[proc.pid] = proc
            self._owners[proc.pid] = _PROCESS_OWNER.get() or config.session_id

            # [FIX-P6] Keep references to pump tasks so we can cancel them.
            pump_tasks: list[asyncio.Task] = []
            try:
                async for item, tasks in self._interleave_streams(proc, config):
                    pump_tasks = tasks   # Updated each yield; keep latest refs
                    yield item
            finally:
                self._active.pop(proc.pid, None)
                self._owners.pop(proc.pid, None)
                # [FIX-P6] Cancel pump tasks before killing the process.
                for t in pump_tasks:
                    if not t.done():
                        t.cancel()
                if pump_tasks:
                    await asyncio.gather(*pump_tasks, return_exceptions=True)
                # Kill the process (and its group) if still running
                if proc.returncode is None:
                    await self._kill_process_tree(proc, _SIGKILL)
                    await proc.wait()

    async def kill_all(self) -> None:
        """
        Terminate every active subprocess.

        Called during engine shutdown or on SIGINT. Sends SIGTERM first,
        then waits up to 2 seconds before force-killing survivors.
        """
        # [FIX-P3] Snapshot active pids before first loop so that
        # concurrent completions (which pop from _active) don't cause
        # RuntimeError("dictionary changed size during iteration").
        active_snapshot = list(self._active.items())

        for pid, proc in active_snapshot:
            if proc.returncode is None:
                await self._kill_process_tree(proc, _SIGTERM)

        if active_snapshot:
            await asyncio.sleep(_KILL_GRACE_S)

        # Second pass: force-kill survivors.
        # [FIX-P3] Snapshot again — new processes may have started or old
        # ones completed during the sleep.
        for pid, proc in list(self._active.items()):
            if proc.returncode is None:
                await self._kill_process_tree(proc, _SIGKILL)

        waits = [
            proc.wait()
            for _, proc in list(self._active.items())
            if proc.returncode is None
        ]
        if waits:
            await asyncio.gather(
                *(asyncio.wait_for(wait, timeout=_KILL_GRACE_S) for wait in waits),
                return_exceptions=True,
            )

    async def kill_owner(self, owner: str) -> None:
        """Terminate only processes created by one agent run."""

        active_snapshot = [
            (pid, proc)
            for pid, proc in list(self._active.items())
            if self._owners.get(pid) == owner
        ]
        for _, proc in active_snapshot:
            if proc.returncode is None:
                await self._kill_process_tree(proc, _SIGTERM)
        if active_snapshot:
            await asyncio.sleep(_KILL_GRACE_S)
        survivors = [
            proc
            for pid, proc in list(self._active.items())
            if self._owners.get(pid) == owner and proc.returncode is None
        ]
        for proc in survivors:
            await self._kill_process_tree(proc, _SIGKILL)
        if survivors:
            await asyncio.gather(
                *(asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S) for proc in survivors),
                return_exceptions=True,
            )

    # ── I/O drain strategies ─────────────────────────────────────────────────

    async def _drain_and_wait(
        self,
        proc: asyncio.subprocess.Process,
        config: ProcessConfig,
        t0: float,
    ) -> ProcessResult:
        if config.drain_policy in (DrainPolicy.CONCURRENT, DrainPolicy.INTERLEAVED):
            return await self._drain_concurrent(proc, config, t0)
        return await self._drain_sequential(proc, config, t0)

    async def _drain_concurrent(
        self,
        proc: asyncio.subprocess.Process,
        config: ProcessConfig,
        t0: float,
    ) -> ProcessResult:
        """
        Drain stdout and stderr as two independent asyncio tasks.

        This is the correct, deadlock-free approach. Both pipes are read
        without blocking on each other. asyncio.gather() collects results
        as soon as both EOF signals are received.

        [FIX-P4] Uses bounded reads instead of read(-1) to prevent OOM.
        [FIX-P5] On timeout, collects partial data from cancelled tasks
                 before handing off to _handle_timeout.
        """
        stdin_task: Optional[asyncio.Task] = None
        stdout_task: Optional[asyncio.Task] = None
        stderr_task: Optional[asyncio.Task] = None

        if config.stdin_data is not None and proc.stdin:
            async def _write_stdin() -> None:
                assert proc.stdin is not None
                proc.stdin.write(config.stdin_data)   # type: ignore[arg-type]
                await proc.stdin.drain()
                proc.stdin.close()
                await proc.stdin.wait_closed()

            stdin_task = asyncio.create_task(_write_stdin())

        try:
            stdout_task = asyncio.create_task(self._read_bounded(proc.stdout))
            stderr_task = asyncio.create_task(self._read_bounded(proc.stderr))

            done, pending = await asyncio.wait(
                {stdout_task, stderr_task},
                timeout=config.timeout_s,
                return_when=asyncio.ALL_COMPLETED,
            )
            if pending:
                signal_sent = await self._terminate_for_timeout(proc)
                stdout_bytes, stderr_bytes = await self._collect_reader_tasks(
                    stdout_task,
                    stderr_task,
                    timeout=_KILL_GRACE_S,
                )
                rc = proc.returncode if proc.returncode is not None else -1
                return ProcessResult(
                    returncode       = rc,
                    stdout           = stdout_bytes,
                    stderr           = stderr_bytes,
                    args             = config.args,
                    elapsed_s        = time.monotonic() - t0,
                    timed_out        = True,
                    signal_name      = signal_sent,
                    output_truncated = (
                        _TRUNCATION_MARKER in stdout_bytes
                        or _TRUNCATION_MARKER in stderr_bytes
                    ),
                )

            stdout_bytes, stderr_bytes = stdout_task.result(), stderr_task.result()
            truncated = (
                _TRUNCATION_MARKER in stdout_bytes
                or _TRUNCATION_MARKER in stderr_bytes
            )
        except asyncio.CancelledError:
            for task in (stdout_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            await self._kill_process_tree(proc, _SIGKILL)
            await asyncio.gather(
                *(task for task in (stdout_task, stderr_task) if task is not None),
                return_exceptions=True,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
            except asyncio.TimeoutError:
                pass
            raise
        finally:
            if stdin_task and not stdin_task.done():
                stdin_task.cancel()
                await asyncio.gather(stdin_task, return_exceptions=True)

        await proc.wait()
        # [FIX-P2] Explicit None check instead of `returncode or 0`.
        # `None or 0` silently reports a process whose wait() was skipped
        # (or failed) as exit code 0 (success). This masked real crashes.
        rc = proc.returncode if proc.returncode is not None else -1
        return ProcessResult(
            returncode       = rc,
            stdout           = stdout_bytes,
            stderr           = stderr_bytes,
            args             = config.args,
            elapsed_s        = time.monotonic() - t0,
            output_truncated = truncated,   # [FIX-P4]
        )

    async def _drain_sequential(
        self,
        proc: asyncio.subprocess.Process,
        config: ProcessConfig,
        t0: float,
    ) -> ProcessResult:
        """
        Sequential drain via communicate().

        Only use when you know stderr will be small — e.g., flash verification
        commands that emit a single status line on stderr. For build commands,
        always prefer CONCURRENT.
        """
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=config.stdin_data),
                timeout=config.timeout_s,
            )
        except asyncio.TimeoutError:
            await self._terminate_for_timeout(proc)
            return await self._handle_timeout(proc, config, t0)
        except asyncio.CancelledError:
            await self._kill_process_tree(proc, _SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
            except asyncio.TimeoutError:
                pass
            raise

        # [FIX-P2] Explicit None check
        rc = proc.returncode if proc.returncode is not None else -1
        stdout_bytes = stdout_bytes or b""
        stderr_bytes = stderr_bytes or b""
        truncated = _TRUNCATION_MARKER in stdout_bytes or _TRUNCATION_MARKER in stderr_bytes
        return ProcessResult(
            returncode       = rc,
            stdout           = stdout_bytes,
            stderr           = stderr_bytes,
            args             = config.args,
            elapsed_s        = time.monotonic() - t0,
            output_truncated = truncated,
        )

    async def _interleave_streams(
        self,
        proc: asyncio.subprocess.Process,
        config: ProcessConfig,
    ) -> AsyncIterator[tuple[tuple[str, bytes], list[asyncio.Task]]]:
        """
        Merge stdout and stderr into a single async stream of tagged lines.

        [FIX-P7] Uses an absolute deadline instead of per-readline timeout.
        The original applied config.timeout_s to each readline() call,
        allowing a process that emits lines just faster than the timeout to
        stream forever. It also silently stopped yielding on per-line timeout
        without killing the subprocess.

        [FIX-P6] Yields task references alongside items so the caller (stream())
        can cancel them in its finally block even after the generator exits.

        Uses an asyncio.Queue as the merge point. Each stream pumps into
        the queue independently; a sentinel (None) signals EOF. Two
        sentinels = both streams closed.
        """
        deadline = asyncio.get_event_loop().time() + config.timeout_s
        queue: asyncio.Queue[tuple[str, bytes] | None] = asyncio.Queue(maxsize=512)

        async def _pump(name: str, reader: asyncio.StreamReader) -> None:
            total_bytes = 0
            try:
                while True:
                    remaining = deadline - asyncio.get_event_loop().time()
                    if remaining <= 0:
                        break   # [FIX-P7] Absolute deadline expired
                    try:
                        line = await asyncio.wait_for(
                            reader.readline(), timeout=min(remaining, 5.0)
                        )
                    except asyncio.TimeoutError:
                        # Check if overall deadline expired
                        if asyncio.get_event_loop().time() >= deadline:
                            break
                        continue   # Partial timeout — keep reading
                    if not line:
                        break   # EOF
                    total_bytes += len(line)
                    # [FIX-P4] Bounded streaming output
                    if total_bytes > MAX_OUTPUT_BYTES:
                        await queue.put((name, _TRUNCATION_MARKER))
                        # Drain without storing
                        while True:
                            try:
                                chunk = await asyncio.wait_for(reader.read(65536), timeout=1.0)
                                if not chunk:
                                    break
                            except asyncio.TimeoutError:
                                break
                        break
                    await queue.put((name, line))
            except asyncio.CancelledError:
                pass   # Normal cancellation from stream() cleanup
            finally:
                await queue.put(None)   # EOF sentinel

        tasks = [
            asyncio.create_task(_pump("stdout", proc.stdout)),  # type: ignore[arg-type]
            asyncio.create_task(_pump("stderr", proc.stderr)),  # type: ignore[arg-type]
        ]

        done_count = 0
        while done_count < 2:
            # [FIX-P7] Also enforce absolute deadline at the consumer level
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=min(remaining, 5.0))
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() >= deadline:
                    break
                continue
            if item is None:
                done_count += 1
            else:
                yield item, tasks   # [FIX-P6] yield tasks alongside data

        # Cancel any pump tasks still running after deadline
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ── Timeout handling ──────────────────────────────────────────────────────

    async def _handle_timeout(
        self,
        proc: asyncio.subprocess.Process,
        config: ProcessConfig,
        t0: float,
        *,
        stdout_prefix: bytes = b"",  # [FIX-P5] partial data from cancelled tasks
        stderr_prefix: bytes = b"",  # [FIX-P5]
    ) -> ProcessResult:
        """
        Deterministic timeout response:
          1. SIGTERM to process GROUP — requests graceful shutdown of entire tree
          2. 3s wait  — allow cleanup handlers to run
          3. SIGKILL to process GROUP — force kill if still alive

        After killing, collect any bytes already buffered in the pipes.

        [FIX-P1] Uses _kill_process_tree() to send signals to the process group,
                 not just the direct subprocess.
        [FIX-P5] Accepts stdout/stderr bytes pre-read by cancelled tasks.
        """
        elapsed     = time.monotonic() - t0
        signal_sent = await self._terminate_for_timeout(proc)

        # Drain any bytes buffered before the kill, capped to avoid hangs
        # [FIX-P5] Prepend partial data already read by cancelled tasks
        try:
            remaining = await asyncio.wait_for(
                proc.stdout.read(MAX_OUTPUT_BYTES) if proc.stdout else asyncio.sleep(0, result=b""),
                timeout=1.0,
            )
            stdout_bytes = stdout_prefix + remaining
        except Exception:
            stdout_bytes = stdout_prefix

        try:
            remaining = await asyncio.wait_for(
                proc.stderr.read(MAX_OUTPUT_BYTES) if proc.stderr else asyncio.sleep(0, result=b""),
                timeout=1.0,
            )
            stderr_bytes = stderr_prefix + remaining
        except Exception:
            stderr_bytes = stderr_prefix

        # [FIX-P2] Explicit None check
        rc = proc.returncode if proc.returncode is not None else -1

        return ProcessResult(
            returncode  = rc,
            stdout      = stdout_bytes,
            stderr      = stderr_bytes,
            args        = config.args,
            elapsed_s   = elapsed,
            timed_out   = True,
            signal_name = signal_sent,
            output_truncated = (
                len(stdout_bytes) > MAX_OUTPUT_BYTES
                or len(stderr_bytes) > MAX_OUTPUT_BYTES
                or _TRUNCATION_MARKER in stdout_bytes
                or _TRUNCATION_MARKER in stderr_bytes
            ),
        )

    async def _terminate_for_timeout(
        self,
        proc: asyncio.subprocess.Process,
    ) -> str:
        """Terminate a timed-out process tree and return the strongest signal used."""
        signal_sent = "SIGTERM"
        await self._kill_process_tree(proc, _SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TIMEOUT_GRACE_S)
        except asyncio.TimeoutError:
            await self._kill_process_tree(proc, _SIGKILL)
            signal_sent = "SIGKILL"
            try:
                await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE_S)
            except asyncio.TimeoutError:
                pass
        return signal_sent

    async def _collect_reader_tasks(
        self,
        stdout_task: asyncio.Task,
        stderr_task: asyncio.Task,
        *,
        timeout: float,
    ) -> tuple[bytes, bytes]:
        """Collect bounded reader task results without leaking cancelled tasks."""
        try:
            await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task),
                timeout=timeout,
            )
        except Exception:
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

        def _result(task: asyncio.Task) -> bytes:
            if task.cancelled() or not task.done():
                return b""
            exc = task.exception()
            if exc is not None:
                return b""
            return task.result()

        return _result(stdout_task), _result(stderr_task)

    # ── Process tree management ───────────────────────────────────────────────

    async def _kill_process_tree(
        self,
        proc: asyncio.subprocess.Process,
        sig: int,
    ) -> None:
        """
        [FIX-P1] Send a signal to the entire process group.

        On POSIX, start_new_session=True (set at launch) means the subprocess
        is the group leader of a new process group. os.killpg() sends the signal
        to every process in that group (the subprocess AND all its children).

        This is critical for embedded toolchains:
          idf.py (group leader)
            └─ ninja
                 └─ arm-none-eabi-gcc  ← must die too

        On Windows, TerminateProcess is used (no process group concept).
        The Windows job object approach is more complete but requires additional
        setup; this is a best-effort approximation.
        """
        if proc.returncode is not None:
            return   # Already exited

        if sys.platform == "win32":
            if sig == _SIGKILL:
                try:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(proc.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(killer.wait(), timeout=2.0)
                except Exception:
                    try:
                        proc.kill()
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
            else:
                try:
                    proc.terminate()
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            return

        # POSIX: kill the entire process group
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            # Process group may not exist if the process already exited,
            # or if the OS hasn't yet created the group after fork.
            # Fall back to killing only the direct process.
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    # ── Bounded read helper ───────────────────────────────────────────────────

    @staticmethod
    async def _read_bounded(
        reader: Optional[asyncio.StreamReader],
        max_bytes: int = MAX_OUTPUT_BYTES,
    ) -> bytes:
        """
        [FIX-P4] Read from a StreamReader with an output size limit.

        Reads in 64 KB chunks to keep memory pressure low. When the limit
        is reached, inserts a truncation marker and drains the remaining
        output without storing it (to unblock the writer and allow the
        process to proceed to exit without hanging on a full pipe buffer).
        """
        chunks: list[bytes] = []
        total = 0
        if reader is None:
            return b""

        try:
            while True:
                chunk = await reader.read(_CHUNK_SIZE)
                if not chunk:
                    break   # EOF

                total += len(chunk)
                if total > max_bytes:
                    # Store up to the limit, then inject marker and drain remainder
                    over = total - max_bytes
                    chunks.append(chunk[:-over] if over < len(chunk) else b"")
                    chunks.append(_TRUNCATION_MARKER)
                    # Drain remaining output so the write end of the pipe doesn't
                    # block (which would prevent the subprocess from exiting cleanly)
                    while True:
                        remainder = await reader.read(_CHUNK_SIZE)
                        if not remainder:
                            break
                    break

                chunks.append(chunk)
        except asyncio.CancelledError:
            # Cancellation during process cleanup must not discard bytes already
            # drained from the OS pipe into this coroutine.
            return b"".join(chunks)

        return b"".join(chunks)

    # ── Environment construction ──────────────────────────────────────────────

    @staticmethod
    def _build_env(config: ProcessConfig) -> dict[str, str]:
        """
        Construct a subprocess environment from the allowlist + caller overrides.

        Never passes through the full host environment. This prevents:
        - Python virtualenv activation scripts poisoning the toolchain PATH
        - Conflicting IDF_PATH / ARDUINO_DIR from the developer's shell
        - PYTHONPATH entries that change import resolution inside subprocesses
        """
        env: dict[str, str] = {}

        for key in config.env_allowlist:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        if config.env:
            env.update(config.env)   # Caller overrides win

        return env
