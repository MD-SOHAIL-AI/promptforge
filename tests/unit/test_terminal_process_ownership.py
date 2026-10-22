"""Real Windows terminal ownership using harmless, owned Python children."""
import asyncio
from collections import deque
import ctypes
from ctypes import wintypes
import os
import re
import sys

import pytest

from backend.services.terminal_service import (
    TerminalService, TerminalSession, _PipeTerminalDriver, _ConPtyTerminalDriver,
)


def alive(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE, read-only
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows job/ConPTY qualification")
@pytest.mark.parametrize("driver_type", [_PipeTerminalDriver, _ConPtyTerminalDriver])
def test_terminal_stop_terminates_its_real_descendants(tmp_path, driver_type):
    if driver_type is _ConPtyTerminalDriver:
        pytest.importorskip("winpty")
    script = "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('owned-child='+str(child.pid),flush=True); time.sleep(60)"
    async def scenario():
        options = {"cols": 80, "rows": 24} if driver_type is _ConPtyTerminalDriver else {}
        driver = await driver_type.spawn((sys.executable, "-u", "-c", script), cwd=tmp_path, **options)
        session = TerminalSession("fixture", "Python", str(tmp_path), driver.process,
            deque(maxlen=20), driver=driver)
        try:
            output = ""
            async def child_pid():
                nonlocal output
                while not re.search(r"owned-child=(\d+)", output):
                    output += await driver.read("stdout")
                return int(re.search(r"owned-child=(\d+)", output).group(1))
            pid = await asyncio.wait_for(child_pid(), timeout=10)
            assert alive(pid)
            await asyncio.wait_for(TerminalService()._stop_session(session), timeout=4)
            for _ in range(100):
                if not alive(pid):
                    break
                await asyncio.sleep(0.02)
            assert not alive(pid), "owned terminal descendant survived shutdown"
            assert session.closed
        finally:
            if not session.closed:
                await TerminalService()._stop_session(session)
    asyncio.run(scenario())
