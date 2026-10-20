"""Shared async cycle driver and phase allocations for every workspace strategy."""
from __future__ import annotations

import asyncio
import inspect
import math
import os
import time
from dataclasses import dataclass

from .canonical_runtime import OrderedToolScheduler
from backend.contracts.runtime_policy import REVIEW_UNIT_SECONDS


@dataclass(frozen=True)
class PhaseBudget:
    build_seconds: float = 0
    review_seconds: float = REVIEW_UNIT_SECONDS
    persistence_seconds: float = 5

    @classmethod
    def for_task(cls, requires_build, durations=(), *, review_count=1, cold=False):
        samples = sorted(float(value) for value in durations if isinstance(value, (int, float)) and math.isfinite(value) and value > 0)
        estimate = samples[min(len(samples) - 1, math.ceil(len(samples) * .95) - 1)] if samples else 120
        if cold:
            estimate = max(300, estimate)
        return cls(build_seconds=min(300, max(120, estimate)) if requires_build else 0,
                   review_seconds=REVIEW_UNIT_SECONDS * max(1, review_count))

    @property
    def generation_reserve(self):
        return self.build_seconds + self.review_seconds

    def generation_reserve_for(self, task_seconds):
        """Reserve a share of the job, rather than most of a short job.

        Phase estimates are ceilings, not time already spent. Actual builds
        and reviews remain bounded by their phase and cumulative job deadlines.
        """
        return min(self.generation_reserve, max(0.0, float(task_seconds)) / 3)

    def build_deadline(self, parent_deadline, *, now=None):
        now = time.monotonic() if now is None else now
        # The parent deadline already excludes persistence time. When callers
        # have less than a full review allocation, use the remaining phase time.
        available = parent_deadline - self.review_seconds
        return min(now + self.build_seconds, available if available > now else parent_deadline)

    def dependency_deadline(self, parent_deadline, *, now=None):
        now = time.monotonic() if now is None else now
        return min(now + 90, parent_deadline)


class AsyncAgentEngine:
    """One cycle/admission seam, with mode-specific workspace adapters.

    Adapters retain source and verification semantics. Compatibility planners
    and blocking host handlers run in threads; router planners stay asynchronous.
    """

    def __init__(self, *, max_parallel_reads=6):
        self.scheduler = OrderedToolScheduler(max_parallel_reads=max_parallel_reads)
        self.phase = "idle"

    async def turns(self, first, last):
        for turn in range(first, min(600, last) + 1):
            self.phase = "context"
            await asyncio.sleep(0)
            yield turn
        self.phase = "idle"

    async def request(self, planner, *, on_summary=None, **kwargs):
        self.phase = "model"
        method = getattr(planner, "request_turn_async", None)
        if callable(method) and shared_engine_enabled():
            async def summary(value):
                if on_summary:
                    result = on_summary(value)
                    if inspect.isawaitable(result):
                        await result
            return await method(**kwargs, stream=True, on_summary=summary)
        method = getattr(planner, "request_turn_stream", None)
        if callable(method):
            loop = asyncio.get_running_loop()
            accepting = True

            def summary(value):
                if not on_summary or not accepting:
                    return
                result = on_summary(value)
                if inspect.isawaitable(result):
                    asyncio.run_coroutine_threadsafe(result, loop).result()

            try:
                return await asyncio.to_thread(method, **kwargs, on_summary=summary)
            finally:
                accepting = False
        return await asyncio.to_thread(planner.request_turn, **kwargs)

    def settle(self, attempt, calls, **kwargs):
        self.phase = "settlement"
        return attempt.settle(calls, **kwargs)

    async def execute(self, invocations, executor):
        self.phase = "tools"
        return await self.scheduler.run(invocations, executor)


def shared_engine_enabled():
    return os.getenv("FORGEX_ENABLE_SHARED_AGENT_ENGINE", "1") == "1"
