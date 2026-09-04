"""Interactive shell sessions for the ForgeX integrated terminal.

Windows sessions are backed by ConPTY through pywinpty. A process-pipe
driver remains available for non-Windows hosts and lightweight test installs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, cast

TerminalStream = Literal["stdout", "stderr", "system"]
TerminalLifecycle = Literal["running", "stopping", "exited", "closed"]
TerminalCapability = Literal["conpty", "pty", "pipe"]
MAX_TERMINAL_INPUT_LENGTH = 20_000


@dataclass(frozen=True, slots=True)
class TerminalProfile:
    profile_id: str
    name: str
    shell: str
    command: tuple[str, ...]
    available: bool = True

    def to_dict(self, *, default: bool, capability: TerminalCapability) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "name": self.name,
            "shell": self.shell,
            "available": self.available,
            "default": default,
            "process_capability": capability,
        }


@dataclass(frozen=True, slots=True)
class TerminalOutputEvent:
    sequence: int
    stream: TerminalStream
    data: str
    timestamp: float
    dropped: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "stream": self.stream,
            "data": self.data,
            "timestamp": self.timestamp,
            "dropped": self.dropped,
        }


class TerminalDriver(Protocol):
    """Small async interface shared by ConPTY and pipe processes."""

    process: object
    capability: TerminalCapability
    output_streams: tuple[TerminalStream, ...]

    @property
    def returncode(self) -> int | None: ...

    async def read(self, stream: TerminalStream, size: int = 4096) -> str: ...

    async def write(self, data: str) -> None: ...

    async def resize(self, cols: int, rows: int) -> None: ...

    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...

    async def kill(self) -> None: ...


@dataclass(slots=True)
class TerminalSession:
    session_id: str
    shell: str
    cwd: str
    process: object
    events: deque[TerminalOutputEvent]
    cols: int = 80
    rows: int = 24
    sequence: int = 0
    closed: bool = False
    profile_id: str = "default"
    title: str = "Terminal"
    process_capability: TerminalCapability = "pipe"
    lifecycle_status: TerminalLifecycle = "running"
    exit_code: int | None = None
    driver: TerminalDriver | None = field(default=None, repr=False)
    subscribers: set[asyncio.Queue[TerminalOutputEvent]] = field(default_factory=set, repr=False)
    subscriber_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    tasks: set[asyncio.Task[object]] = field(default_factory=set, repr=False)
    exit_notified: bool = field(default=False, repr=False)


class _PipeTerminalDriver:
    capability: TerminalCapability = "pipe"
    output_streams: tuple[TerminalStream, ...] = ("stdout", "stderr")

    def __init__(self, process: object) -> None:
        self.process = process

    @classmethod
    async def spawn(cls, command: tuple[str, ...], *, cwd: Path) -> _PipeTerminalDriver:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
            **_creation_kwargs(),
        )
        return cls(process)

    @property
    def returncode(self) -> int | None:
        return cast(int | None, getattr(self.process, "returncode", None))

    async def read(self, stream: TerminalStream, size: int = 4096) -> str:
        reader = getattr(self.process, stream, None)
        if reader is None:
            return ""
        chunk = await reader.read(size)
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8", errors="replace")
        return str(chunk or "")

    async def write(self, data: str) -> None:
        stdin = getattr(self.process, "stdin", None)
        if stdin is None:
            raise ValueError("terminal stdin is unavailable")
        stdin.write(data.encode("utf-8", errors="replace"))
        drain = getattr(stdin, "drain", None)
        if drain is not None:
            await drain()

    async def resize(self, cols: int, rows: int) -> None:
        del cols, rows
        # Ordinary subprocess pipes do not expose a terminal to resize.

    async def wait(self) -> int:
        return int(await getattr(self.process, "wait")())

    async def terminate(self) -> None:
        getattr(self.process, "terminate")()

    async def kill(self) -> None:
        try:
            getattr(self.process, "kill")()
        except ProcessLookupError:
            pass


class _ConPtyTerminalDriver:
    capability: TerminalCapability = "conpty"
    output_streams: tuple[TerminalStream, ...] = ("stdout",)

    def __init__(self, process: object) -> None:
        self.process = process
        # Prime pywinpty's process status once before the dedicated reader
        # takes ownership of the native output handle.
        getattr(process, "isalive")()
        self._loop = asyncio.get_running_loop()
        self._output: asyncio.Queue[str | None] = asyncio.Queue()
        self._exited = asyncio.Event()
        self._exit_code: int | None = None
        self._reader = threading.Thread(
            target=self._read_forever,
            name=f"forgex-conpty-{getattr(process, 'pid', 'shell')}",
            daemon=True,
        )
        self._reader.start()

    @classmethod
    async def spawn(
        cls,
        command: tuple[str, ...],
        *,
        cwd: Path,
        cols: int,
        rows: int,
    ) -> _ConPtyTerminalDriver:
        from winpty import PtyProcess

        process = PtyProcess.spawn(
            list(command),
            cwd=str(cwd),
            env=os.environ.copy(),
            dimensions=(rows, cols),
        )
        return cls(process)

    @property
    def returncode(self) -> int | None:
        return self._exit_code

    async def read(self, stream: TerminalStream, size: int = 4096) -> str:
        del stream, size
        data = await self._output.get()
        return data or ""

    def _read_forever(self) -> None:
        while True:
            try:
                data = str(getattr(self.process, "read")(4096))
            except EOFError:
                self._publish_exit()
                return
            except OSError:
                self._publish_exit()
                return
            if data:
                self._publish(data)
                continue
            # pywinpty can emit an internal keepalive marker that its wrapper
            # translates to an empty string. Empty is not EOF while alive.
            time.sleep(0.05)

    def _publish(self, data: str | None) -> None:
        try:
            self._loop.call_soon_threadsafe(self._output.put_nowait, data)
        except RuntimeError:
            pass

    def _publish_exit(self) -> None:
        try:
            getattr(self.process, "isalive")()
        except Exception:
            pass
        value = getattr(self.process, "exitstatus", None)
        self._exit_code = int(value) if isinstance(value, int) else 0
        try:
            self._loop.call_soon_threadsafe(self._exited.set)
        except RuntimeError:
            pass
        self._publish(None)

    async def write(self, data: str) -> None:
        try:
            getattr(self.process, "write")(data)
        except (EOFError, OSError) as exc:
            raise ValueError("terminal session is closed") from exc

    async def resize(self, cols: int, rows: int) -> None:
        # pywinpty follows the POSIX order: rows, then columns.
        getattr(self.process, "setwinsize")(rows, cols)

    async def wait(self) -> int:
        # read() receives EOF when the pseudoterminal exits. Avoid periodic
        # PtyProcess.isalive() probes: they contend with the native read path
        # and make interactive input visibly stall under pywinpty.
        await self._exited.wait()
        return self._exit_code or 0

    async def terminate(self) -> None:
        await asyncio.to_thread(getattr(self.process, "terminate"), False)

    async def kill(self) -> None:
        close = getattr(self.process, "close", None)
        if close is not None:
            await asyncio.to_thread(close, True)
            return
        await asyncio.to_thread(getattr(self.process, "terminate"), True)


class _PosixPtyTerminalDriver(_ConPtyTerminalDriver):
    """POSIX pseudoterminal backed by ptyprocess."""

    capability: TerminalCapability = "pty"

    @classmethod
    async def spawn(
        cls,
        command: tuple[str, ...],
        *,
        cwd: Path,
        cols: int,
        rows: int,
    ) -> _PosixPtyTerminalDriver:
        from ptyprocess import PtyProcessUnicode

        process = PtyProcessUnicode.spawn(
            list(command),
            cwd=str(cwd),
            env=os.environ.copy(),
            dimensions=(rows, cols),
        )
        return cls(process)


class TerminalService:
    """Manage bounded, interactive shell sessions."""

    def __init__(self, *, max_events: int = 2000) -> None:
        self._max_events = max_events
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

    def profiles(self) -> tuple[list[TerminalProfile], str]:
        profiles = _terminal_profiles()
        available = [profile for profile in profiles if profile.available]
        if not available:
            available = profiles
        return available, _default_profile_id(available)

    @property
    def default_process_capability(self) -> TerminalCapability:
        if _conpty_available():
            return "conpty"
        return "pty" if _posix_pty_available() else "pipe"

    async def start(
        self,
        cwd: str | Path,
        *,
        cols: int = 80,
        rows: int = 24,
        profile_id: str | None = None,
        title: str | None = None,
    ) -> TerminalSession:
        root = _validated_cwd(cwd)
        safe_cols = _clamp(cols, 20, 300)
        safe_rows = _clamp(rows, 5, 120)
        profiles, default_profile_id = self.profiles()
        selected_id = profile_id or default_profile_id
        profile = next((item for item in profiles if item.profile_id == selected_id), None)
        if profile is None or not profile.available:
            raise ValueError(f"terminal profile is unavailable: {selected_id}")

        driver = await self._spawn_driver(profile, root, safe_cols, safe_rows)
        session = TerminalSession(
            session_id=f"term-{uuid.uuid4().hex}",
            shell=profile.shell,
            cwd=str(root),
            process=driver.process,
            events=deque(maxlen=self._max_events),
            cols=safe_cols,
            rows=safe_rows,
            profile_id=profile.profile_id,
            title=_validated_title(title or profile.name),
            process_capability=driver.capability,
            lifecycle_status="running",
            driver=driver,
        )
        async with self._lock:
            self._sessions[session.session_id] = session

        for stream in driver.output_streams:
            self._track(session, self._pump(session, stream), f"{session.session_id}-{stream}")
        self._track(session, self._watch(session), f"{session.session_id}-watch")
        return session

    async def _spawn_driver(
        self,
        profile: TerminalProfile,
        cwd: Path,
        cols: int,
        rows: int,
    ) -> TerminalDriver:
        if sys.platform == "win32":
            try:
                return await _ConPtyTerminalDriver.spawn(
                    profile.command,
                    cwd=cwd,
                    cols=cols,
                    rows=rows,
                )
            except ImportError:
                # Minimal source installs can intentionally omit the wheel.
                pass
        else:
            try:
                return await _PosixPtyTerminalDriver.spawn(
                    profile.command,
                    cwd=cwd,
                    cols=cols,
                    rows=rows,
                )
            except ImportError:
                pass
        return await _PipeTerminalDriver.spawn(profile.command, cwd=cwd)

    async def get(self, session_id: str) -> TerminalSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def write(self, session_id: str, data: str) -> TerminalSession:
        session = await self._required(session_id)
        driver = self._driver(session)
        if session.closed or driver.returncode is not None:
            raise ValueError("terminal session is closed")
        if "\x00" in data:
            raise ValueError("terminal input cannot contain NUL characters")
        if len(data) > MAX_TERMINAL_INPUT_LENGTH:
            raise ValueError(
                f"terminal input cannot exceed {MAX_TERMINAL_INPUT_LENGTH} characters"
            )
        await driver.write(data)
        return session

    async def clear(self, session_id: str) -> TerminalSession:
        session = await self._required(session_id)
        session.events.clear()
        # Sequence numbers stay monotonic, so reconnect cursors remain valid.
        return session

    async def rename(self, session_id: str, title: str) -> TerminalSession:
        session = await self._required(session_id)
        session.title = _validated_title(title)
        return session

    async def resize(self, session_id: str, cols: int, rows: int) -> TerminalSession:
        session = await self._required(session_id)
        safe_cols = _clamp(cols, 20, 300)
        safe_rows = _clamp(rows, 5, 120)
        await self._driver(session).resize(safe_cols, safe_rows)
        session.cols = safe_cols
        session.rows = safe_rows
        return session

    async def stop(self, session_id: str) -> None:
        session = await self._required(session_id)
        await self._stop_session(session)
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def stop_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        await asyncio.gather(*(self._stop_session(session) for session in sessions), return_exceptions=True)

    async def output(
        self,
        session_id: str,
        after: int = 0,
    ) -> tuple[TerminalSession, list[TerminalOutputEvent]]:
        session = await self._required(session_id)
        return session, [event for event in session.events if event.sequence > after]

    async def subscribe(
        self,
        session_id: str,
        after: int = 0,
    ) -> tuple[TerminalSession, list[TerminalOutputEvent], asyncio.Queue[TerminalOutputEvent]]:
        session = await self._required(session_id)
        queue: asyncio.Queue[TerminalOutputEvent] = asyncio.Queue(maxsize=500)
        with session.subscriber_lock:
            replay = [event for event in session.events if event.sequence > after]
            session.subscribers.add(queue)
        return session, replay, queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[TerminalOutputEvent]) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                with session.subscriber_lock:
                    session.subscribers.discard(queue)

    async def _required(self, session_id: str) -> TerminalSession:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _driver(self, session: TerminalSession) -> TerminalDriver:
        if session.driver is None:
            # Compatibility for tests/callers that construct a session around
            # an asyncio-like process directly.
            session.driver = _PipeTerminalDriver(session.process)
        return session.driver

    def _track(
        self,
        session: TerminalSession,
        awaitable: Coroutine[Any, Any, object],
        name: str,
    ) -> None:
        task = asyncio.create_task(awaitable, name=name)
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)

    async def _pump(self, session: TerminalSession, stream: TerminalStream) -> None:
        driver = self._driver(session)
        while True:
            chunk = await driver.read(stream)
            if not chunk:
                return
            self._append(session, stream, chunk)

    async def _watch(self, session: TerminalSession) -> None:
        returncode = await self._driver(session).wait()
        session.exit_code = returncode
        session.closed = True
        if session.lifecycle_status != "closed":
            session.lifecycle_status = "exited"
        self._notify_exit(session, returncode)

    async def _stop_session(self, session: TerminalSession) -> None:
        driver = self._driver(session)
        if session.closed or driver.returncode is not None:
            session.exit_code = driver.returncode
            session.closed = True
            session.lifecycle_status = "closed"
            self._notify_exit(session, session.exit_code or 0)
            return
        session.lifecycle_status = "stopping"
        try:
            await driver.terminate()
            returncode = await asyncio.wait_for(driver.wait(), timeout=2.0)
        except Exception:
            await asyncio.gather(driver.kill(), return_exceptions=True)
            results = await asyncio.gather(driver.wait(), return_exceptions=True)
            returncode = results[0] if results and isinstance(results[0], int) else driver.returncode
        session.exit_code = int(returncode) if isinstance(returncode, int) else driver.returncode
        session.closed = True
        session.lifecycle_status = "closed"
        self._notify_exit(session, session.exit_code or 0)
        pending = [
            task
            for task in tuple(session.tasks)
            if task is not asyncio.current_task() and not task.done()
        ]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def _notify_exit(self, session: TerminalSession, returncode: int) -> None:
        if session.exit_notified:
            return
        session.exit_notified = True
        self._append(session, "system", f"\r\nShell exited with code {returncode}\r\n")

    def _append(self, session: TerminalSession, stream: TerminalStream, data: str) -> None:
        session.sequence += 1
        event = TerminalOutputEvent(
            sequence=session.sequence,
            stream=stream,
            data=data,
            timestamp=time.time(),
        )
        session.events.append(event)
        with session.subscriber_lock:
            subscribers = tuple(session.subscribers)
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(TerminalOutputEvent(
                        sequence=event.sequence,
                        stream=event.stream,
                        data=event.data,
                        timestamp=event.timestamp,
                        dropped=event.dropped + 1,
                    ))
                except asyncio.QueueFull:
                    pass


def _validated_cwd(cwd: str | Path) -> Path:
    path = Path(cwd).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"terminal cwd does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"terminal cwd is not a directory: {path}")
    return path


def _posix_pty_available() -> bool:
    if sys.platform == "win32":
        return False
    try:
        import ptyprocess  # noqa: F401
    except ImportError:
        return False
    return True


def _validated_title(title: str) -> str:
    value = title.strip()
    if not value:
        raise ValueError("terminal title cannot be empty")
    if len(value) > 80:
        raise ValueError("terminal title cannot exceed 80 characters")
    if any(ord(character) < 32 for character in value):
        raise ValueError("terminal title cannot contain control characters")
    return value


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return min(maximum, max(minimum, int(value)))


def _terminal_profiles() -> list[TerminalProfile]:
    if sys.platform == "win32":
        return [
            TerminalProfile(
                profile_id="powershell",
                name="PowerShell",
                shell="PowerShell",
                command=("powershell.exe", "-NoLogo"),
                available=shutil.which("powershell.exe") is not None,
            ),
            TerminalProfile(
                profile_id="command-prompt",
                name="Command Prompt",
                shell="Command Prompt",
                command=("cmd.exe", "/Q"),
                available=shutil.which("cmd.exe") is not None,
            ),
        ]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return [
        TerminalProfile(
            profile_id="shell",
            name=Path(shell).name,
            shell=Path(shell).name,
            command=(shell, "-i"),
            available=shutil.which(shell) is not None or Path(shell).is_file(),
        )
    ]


def _default_profile_id(profiles: list[TerminalProfile]) -> str:
    preferred = "powershell" if sys.platform == "win32" else "shell"
    if any(profile.profile_id == preferred and profile.available for profile in profiles):
        return preferred
    return profiles[0].profile_id


def _conpty_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winpty  # noqa: F401
    except ImportError:
        return False
    return True


def _creation_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        import subprocess

        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
