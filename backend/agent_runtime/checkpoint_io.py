"""Seal checkpoints without blocking the API event loop or racing cancellation."""
from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from .checkpoint_files import copy_checkpoint_tree


async def seal_checkpoint(service, stage, checkpoint, workspace_revision):
    def seal():
        started = time.monotonic()
        snapshot = service.create_stage("checkpoint-" + uuid.uuid4().hex)
        try:
            copy_checkpoint_tree(stage, snapshot)
            copied = time.monotonic()
            revision = service.inspect_workspace(snapshot).revision
            finished = time.monotonic()
            return {**checkpoint, "stage_path": str(snapshot), "stage_revision": revision,
                    "workspace_revision": workspace_revision, "stage_mode": "full",
                    "io_timings": {"copy_ms": round((copied - started) * 1000),
                        "inspection_ms": round((finished - copied) * 1000),
                        "total_ms": round((finished - started) * 1000)}}
        except BaseException:
            remove_owned(snapshot)
            raise

    def remove_owned(snapshot):
        snapshot = Path(snapshot)
        if snapshot.parent.resolve() != service.staging_root.resolve() or snapshot.is_symlink():
            raise ValueError("AGENT_RESUME_STAGE_UNOWNED")
        shutil.rmtree(snapshot)

    worker = asyncio.create_task(asyncio.to_thread(seal))
    cancelled = False
    # Cancellation must not let stage finalization race an unfinished copy.
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        cleanup = asyncio.create_task(asyncio.to_thread(remove_owned, result["stage_path"]))
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                pass
        cleanup.result()
        raise asyncio.CancelledError()
    return result
