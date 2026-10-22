"""Await filesystem workers inside the owning workspace barrier."""
from __future__ import annotations

import asyncio


async def workspace_io(operation, *args, on_cancel=None, **kwargs):
    """Drain a worker before cancellation can release or remove its workspace.

    Plain to_thread cancellation abandons the await while the thread continues.
    Callers that publish a new artifact can remove that artifact through on_cancel.
    Workers may commit detached durable views. Callers adopt mutable live state
    on the owning event-loop thread after the worker completes.
    """
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
            if worker.done() and worker.cancelled():
                raise
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        if on_cancel is not None:
            await workspace_io(on_cancel, result)
        raise asyncio.CancelledError()
    return result
