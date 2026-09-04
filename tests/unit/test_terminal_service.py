from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from backend.services.terminal_service import TerminalService, TerminalSession


class DummyProcess:
    returncode = None
    stdin = None
    stdout = None
    stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


class RecordingDriver:
    capability = "conpty"
    output_streams = ()

    def __init__(self) -> None:
        self.process = DummyProcess()
        self.resize_calls: list[tuple[int, int]] = []

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def read(self, stream: str, size: int = 4096) -> str:
        del stream, size
        return ""

    async def write(self, data: str) -> None:
        del data

    async def resize(self, cols: int, rows: int) -> None:
        self.resize_calls.append((cols, rows))

    async def wait(self) -> int:
        return await self.process.wait()

    async def terminate(self) -> None:
        self.process.terminate()

    async def kill(self) -> None:
        self.process.kill()


def make_session(session_id: str = "term-test") -> TerminalSession:
    return TerminalSession(
        session_id=session_id,
        shell="PowerShell",
        cwd=str(Path.cwd()),
        process=DummyProcess(),  # type: ignore[arg-type]
        events=deque(maxlen=20),
    )


def test_terminal_resize_updates_session_dimensions() -> None:
    async def run() -> tuple[int, int, list[tuple[int, int]]]:
        service = TerminalService()
        session = make_session()
        driver = RecordingDriver()
        session.driver = driver  # type: ignore[assignment]
        service._sessions[session.session_id] = session  # noqa: SLF001
        resized = await service.resize(session.session_id, 132, 43)
        return resized.cols, resized.rows, driver.resize_calls

    assert asyncio.run(run()) == (132, 43, [(132, 43)])


def test_terminal_resize_clamps_before_reaching_driver() -> None:
    async def run() -> tuple[int, int, list[tuple[int, int]]]:
        service = TerminalService()
        session = make_session()
        driver = RecordingDriver()
        session.driver = driver  # type: ignore[assignment]
        service._sessions[session.session_id] = session  # noqa: SLF001
        resized = await service.resize(session.session_id, 999, 1)
        return resized.cols, resized.rows, driver.resize_calls

    assert asyncio.run(run()) == (300, 5, [(300, 5)])


def test_terminal_subscriber_receives_output_events() -> None:
    async def run() -> str:
        service = TerminalService()
        session = make_session()
        service._sessions[session.session_id] = session  # noqa: SLF001
        subscribed, replay, queue = await service.subscribe(session.session_id)
        assert subscribed is session
        assert replay == []
        service._append(session, "stdout", "hello\r\n")  # noqa: SLF001
        event = await asyncio.wait_for(queue.get(), timeout=1)
        await service.unsubscribe(session.session_id, queue)
        return event.data

    assert asyncio.run(run()) == "hello\r\n"


def test_terminal_subscribe_replays_events_after_cursor() -> None:
    async def run() -> list[str]:
        service = TerminalService()
        session = make_session()
        service._sessions[session.session_id] = session  # noqa: SLF001
        service._append(session, "stdout", "one")  # noqa: SLF001
        service._append(session, "stdout", "two")  # noqa: SLF001
        _, replay, queue = await service.subscribe(session.session_id, after=1)
        await service.unsubscribe(session.session_id, queue)
        return [event.data for event in replay]

    assert asyncio.run(run()) == ["two"]


def test_terminal_subscriber_marks_slow_consumer_overflow() -> None:
    async def run() -> tuple[int, int, int]:
        service = TerminalService()
        session = make_session()
        service._sessions[session.session_id] = session  # noqa: SLF001
        _, _, queue = await service.subscribe(session.session_id)
        for index in range(501):
            service._append(session, "stdout", str(index))  # noqa: SLF001
        queued = []
        while not queue.empty():
            queued.append(queue.get_nowait())
        return len(queued), queued[0].sequence, queued[-1].dropped

    assert asyncio.run(run()) == (500, 2, 1)


def test_terminal_write_rejects_websocket_sized_overflow_and_nul() -> None:
    async def run() -> tuple[str, str]:
        service = TerminalService()
        session = make_session()
        session.driver = RecordingDriver()  # type: ignore[assignment]
        service._sessions[session.session_id] = session  # noqa: SLF001
        messages = []
        for payload in ("x" * 20_001, "bad\x00input"):
            try:
                await service.write(session.session_id, payload)
            except ValueError as exc:
                messages.append(str(exc))
        return messages[0], messages[1]

    oversized, nul = asyncio.run(run())
    assert "20_000" not in oversized
    assert "20000" in oversized
    assert "NUL" in nul


def test_terminal_clear_keeps_sequence_monotonic_for_reconnect() -> None:
    async def run() -> tuple[int, list[int]]:
        service = TerminalService()
        session = make_session()
        service._sessions[session.session_id] = session  # noqa: SLF001
        service._append(session, "stdout", "before")  # noqa: SLF001
        await service.clear(session.session_id)
        service._append(session, "stdout", "after")  # noqa: SLF001
        _, events = await service.output(session.session_id, after=1)
        return session.sequence, [event.sequence for event in events]

    assert asyncio.run(run()) == (2, [2])


def test_terminal_profiles_include_an_available_default() -> None:
    service = TerminalService()
    profiles, default_profile_id = service.profiles()

    assert profiles
    assert default_profile_id in {profile.profile_id for profile in profiles}
    assert all(profile.name and profile.shell for profile in profiles)


def test_terminal_rename_validates_and_updates_title() -> None:
    async def run() -> str:
        service = TerminalService()
        session = make_session()
        service._sessions[session.session_id] = session  # noqa: SLF001
        renamed = await service.rename(session.session_id, "Build shell")
        return renamed.title

    assert asyncio.run(run()) == "Build shell"
