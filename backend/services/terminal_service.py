"""Interactive shell sessions for the ForgeX integrated terminal."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

TerminalStream = Literal["stdout", "stderr", "system"]


@dataclass(frozen=True, slots=True)
class TerminalOutputEvent:
    sequence: int
    stream: TerminalStream
    data: str
    timestamp: float

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "stream": self.stream,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass(slots=True)
class TerminalSession:
    session_id: str
    shell: str
    cwd: str
    process: asyncio.subprocess.Process
    events: deque[TerminalOutputEvent]
    sequence: int = 0
    closed: bool = False


class TerminalService:
    """Manage bounded process-backed shell sessions."""

    def __init__(self, *, max_events: int = 2000) -> None:
        self._max_events = max_events
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

    async def start(self, cwd: str | Path) -> TerminalSession:
        root = _validated_cwd(cwd)
        shell, args = _shell_command()
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(root),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
            **_creation_kwargs(),
        )
        session = TerminalSession(
            session_id=f"term-{uuid.uuid4().hex}",
            shell=shell,
            cwd=str(root),
            process=process,
            events=deque(maxlen=self._max_events),
        )
        async with self._lock:
            self._sessions[session.session_id] = session
        asyncio.create_task(self._pump(session, "stdout"), name=f"{session.session_id}-stdout")
        asyncio.create_task(self._pump(session, "stderr"), name=f"{session.session_id}-stderr")
        asyncio.create_task(self._watch(session), name=f"{session.session_id}-watch")
        return session

    async def get(self, session_id: str) -> TerminalSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def write(self, session_id: str, data: str) -> TerminalSession:
        session = await self._required(session_id)
        if session.closed or session.process.returncode is not None:
            raise ValueError("terminal session is closed")
        if "\x00" in data:
            raise ValueError("terminal input cannot contain NUL characters")
        if session.process.stdin is None:
            raise ValueError("terminal stdin is unavailable")
        session.process.stdin.write(data.encode("utf-8", errors="replace"))
        await session.process.stdin.drain()
        return session

    async def clear(self, session_id: str) -> TerminalSession:
        session = await self._required(session_id)
        session.events.clear()
        session.sequence = 0
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

    async def output(self, session_id: str, after: int = 0) -> tuple[TerminalSession, list[TerminalOutputEvent]]:
        session = await self._required(session_id)
        return session, [event for event in session.events if event.sequence > after]

    async def _required(self, session_id: str) -> TerminalSession:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    async def _pump(self, session: TerminalSession, stream: TerminalStream) -> None:
        reader = session.process.stdout if stream == "stdout" else session.process.stderr
        if reader is None:
            return
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                return
            self._append(session, stream, chunk.decode("utf-8", errors="replace"))

    async def _watch(self, session: TerminalSession) -> None:
        returncode = await session.process.wait()
        session.closed = True
        self._append(session, "system", f"\r\nShell exited with code {returncode}\r\n")

    async def _stop_session(self, session: TerminalSession) -> None:
        if session.process.returncode is not None:
            session.closed = True
            return
        try:
            session.process.terminate()
            await asyncio.wait_for(session.process.wait(), timeout=2.0)
        except Exception:
            try:
                session.process.kill()
            except ProcessLookupError:
                pass
            await asyncio.gather(session.process.wait(), return_exceptions=True)
        session.closed = True

    def _append(self, session: TerminalSession, stream: TerminalStream, data: str) -> None:
        session.sequence += 1
        session.events.append(
            TerminalOutputEvent(
                sequence=session.sequence,
                stream=stream,
                data=data,
                timestamp=time.time(),
            )
        )


def _validated_cwd(cwd: str | Path) -> Path:
    path = Path(cwd).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"terminal cwd does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"terminal cwd is not a directory: {path}")
    return path


def _shell_command() -> tuple[str, list[str]]:
    if sys.platform == "win32":
        return "PowerShell", [
            "powershell.exe",
            "-NoLogo",
            "-NoExit",
            "-Command",
            "function global:prompt { \"PS $($executionContext.SessionState.Path.CurrentLocation)> \" }",
        ]
    shell = os.environ.get("SHELL") or "/bin/sh"
    return Path(shell).name, [shell, "-i"]


def _creation_kwargs() -> dict[str, object]:
    if sys.platform == "win32":
        import subprocess

        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    return {"start_new_session": True}
