import asyncio
import sys

import pytest

from backend.runtime.process_owner import owned_operation
from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager


def test_cancelling_one_owner_leaves_other_process_running():
    async def scenario():
        manager = SubprocessManager()
        config = ProcessConfig(args=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=40)
        first = asyncio.create_task(owned_operation("first", manager.run(config)))
        second = asyncio.create_task(owned_operation("second", manager.run(config)))
        try:
            for _ in range(200):
                if len(manager._active) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(manager._active) == 2
            await asyncio.wait_for(manager.cancel_owner("first"), 5)
            result = await asyncio.wait_for(first, 5)
            assert result.returncode != 0
            assert not second.done(), second.result()
            assert "second" in manager._owners.values()
            with pytest.raises(asyncio.CancelledError):
                await owned_operation("first", manager.run(config))
        finally:
            await manager.cancel_owner("first")
            await manager.cancel_owner("second")
            await asyncio.gather(first, second, return_exceptions=True)
    asyncio.run(scenario())
