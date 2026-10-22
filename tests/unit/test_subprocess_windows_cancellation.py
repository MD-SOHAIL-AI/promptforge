import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.runtime import subprocess_mgr


@pytest.mark.parametrize("outcome", [0, 1, 128, "timeout", "spawn_error"])
def test_windows_taskkill_failure_falls_back_to_owned_handle(monkeypatch, outcome):
    async def scenario():
        target = SimpleNamespace(pid=12345, returncode=None, kill=Mock())
        helper = SimpleNamespace(returncode=None, kill=Mock())

        async def wait():
            if outcome == "timeout" and helper.wait.await_count == 1:
                raise asyncio.TimeoutError()
            helper.returncode = outcome if isinstance(outcome, int) else 1
            return helper.returncode

        helper.wait = AsyncMock(side_effect=wait)
        spawn = AsyncMock(return_value=helper)
        if outcome == "spawn_error":
            spawn.side_effect = OSError("taskkill unavailable")
        monkeypatch.setattr(subprocess_mgr, "sys", SimpleNamespace(platform="win32"))
        monkeypatch.setattr(subprocess_mgr.asyncio, "create_subprocess_exec", spawn)

        await subprocess_mgr.SubprocessManager()._kill_process_tree(
            target, subprocess_mgr._SIGKILL
        )

        spawn.assert_awaited_once_with(
            "taskkill", "/PID", "12345", "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert target.kill.call_count == (0 if outcome == 0 else 1)
        assert helper.kill.call_count == (1 if outcome == "timeout" else 0)
        assert helper.wait.await_count == (
            0 if outcome == "spawn_error" else 2 if outcome == "timeout" else 1
        )

    asyncio.run(scenario())
