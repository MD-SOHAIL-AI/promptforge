"""Finite cleanup even when an injected driver ignores cancellation."""
from __future__ import annotations

import asyncio


async def settle_bounded(awaitable, timeout: float):
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait({task}, timeout=max(0, timeout))
        if task in done:
            return True, task.result()
        return False, None
    finally:
        if not task.done():
            task.cancel()
        # Never await an uncooperative cancellation beyond the cleanup budget.
        task.add_done_callback(lambda value: None if value.cancelled() else value.exception())
