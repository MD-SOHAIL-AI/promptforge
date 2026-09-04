"""Serial SSE fan-out with bounded replay."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass

from ..runtime.serial_runtime import SerialObservation
from .serial_service import SerialService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SerialStreamEvent:
    sequence: int
    observation: SerialObservation

    def to_dict(self) -> dict[str, object]:
        item = self.observation
        return {
            "sequence": self.sequence,
            "timestamp": item.timestamp,
            "line": item.line,
            "source": item.source.value,
            "port": item.port,
            "metadata": dict(item.metadata),
        }


class SerialStreamBroker:
    """Relay the shared SerialService monitor into replayable SSE events."""

    def __init__(self, *, max_events: int = 2_000, subscriber_queue_size: int = 500) -> None:
        self._events: deque[SerialStreamEvent] = deque(maxlen=max_events)
        self._subscriber_queue_size = subscriber_queue_size
        self._subscribers: set[asyncio.Queue[SerialStreamEvent]] = set()
        self._sequence = 0
        self._task: asyncio.Task[None] | None = None
        self._service: SerialService | None = None
        self._lock = asyncio.Lock()

    async def attach(self, service: SerialService) -> None:
        async with self._lock:
            if self._service is service and self._task is not None and not self._task.done():
                return
            prior = self._task
            self._service = service
            self._task = (
                asyncio.create_task(self._pump(service), name="forgex-serial-stream")
                if callable(getattr(service, "monitor", None))
                else None
            )
        if prior is not None and not prior.done():
            prior.cancel()
            await asyncio.gather(prior, return_exceptions=True)

    async def detach(self) -> None:
        async with self._lock:
            task = self._task
            self._task = None
            self._service = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def subscribe(
        self,
        after: int = 0,
    ) -> tuple[list[SerialStreamEvent], asyncio.Queue[SerialStreamEvent]]:
        queue: asyncio.Queue[SerialStreamEvent] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        async with self._lock:
            replay = [event for event in self._events if event.sequence > after]
            self._subscribers.add(queue)
        return replay, queue

    async def unsubscribe(self, queue: asyncio.Queue[SerialStreamEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def _pump(self, service: SerialService) -> None:
        try:
            async for observation in service.monitor():
                await self._publish(observation)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("serial stream pump stopped unexpectedly")

    async def _publish(self, observation: SerialObservation) -> None:
        async with self._lock:
            self._sequence += 1
            event = SerialStreamEvent(self._sequence, observation)
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
