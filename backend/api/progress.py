"""Bounded process-local execution progress transport."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schemas.websocket import ExecutionEvent, ExecutionEventType


@dataclass(slots=True)
class _TaskStream:
    history: deque[ExecutionEvent]
    subscribers: set[asyncio.Queue[ExecutionEvent]] = field(default_factory=set)
    sequence: int = 0
    active: bool = False
    completed_at: float | None = None


class ExecutionProgressHub:
    """Fan out bounded per-task event streams to WebSocket subscribers."""

    def __init__(
        self,
        *,
        history_size: int = 256,
        queue_size: int = 64,
        retention_s: float = 900.0,
    ) -> None:
        self._history_size = history_size
        self._queue_size = queue_size
        self._retention_s = retention_s
        self._streams: dict[str, _TaskStream] = {}
        self._lock = asyncio.Lock()

    async def begin(self, task_id: str) -> bool:
        async with self._lock:
            self._prune_locked()
            stream = self._streams.get(task_id)
            if stream is not None and (stream.active or stream.completed_at is not None):
                return False
            if stream is None:
                stream = self._new_stream()
                self._streams[task_id] = stream
            stream.active = True
            return True

    async def emit(
        self,
        *,
        event: str,
        task_id: str,
        execution_id: str,
        workflow_correlation_id: str,
        payload: dict[str, Any] | None = None,
    ) -> ExecutionEvent:
        event_type = ExecutionEventType(event)
        async with self._lock:
            stream = self._streams.setdefault(task_id, self._new_stream())
            stream.sequence += 1
            message = ExecutionEvent(
                sequence=stream.sequence,
                event=event_type,
                timestamp=datetime.now(timezone.utc),
                task_id=task_id,
                execution_id=execution_id,
                workflow_correlation_id=workflow_correlation_id,
                payload=payload or {},
            )
            stream.history.append(message)
            for queue in tuple(stream.subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(message)
            if event_type in {
                ExecutionEventType.WORKFLOW_COMPLETED,
                ExecutionEventType.WORKFLOW_FAILED,
                ExecutionEventType.WORKFLOW_CANCELLED,
            }:
                stream.active = False
                stream.completed_at = time.monotonic()
            return message

    async def subscribe(
        self,
        task_id: str,
        *,
        after: int = 0,
    ) -> tuple[asyncio.Queue[ExecutionEvent], list[ExecutionEvent]]:
        async with self._lock:
            self._prune_locked()
            stream = self._streams.setdefault(task_id, self._new_stream())
            queue: asyncio.Queue[ExecutionEvent] = asyncio.Queue(self._queue_size)
            stream.subscribers.add(queue)
            replay = [item for item in stream.history if item.sequence > after]
            return queue, replay

    async def unsubscribe(
        self,
        task_id: str,
        queue: asyncio.Queue[ExecutionEvent],
    ) -> None:
        async with self._lock:
            stream = self._streams.get(task_id)
            if stream is not None:
                stream.subscribers.discard(queue)

    def _new_stream(self) -> _TaskStream:
        return _TaskStream(history=deque(maxlen=self._history_size))

    def _prune_locked(self) -> None:
        now = time.monotonic()
        expired = [
            task_id
            for task_id, stream in self._streams.items()
            if not stream.active
            and not stream.subscribers
            and stream.completed_at is not None
            and now - stream.completed_at >= self._retention_s
        ]
        for task_id in expired:
            self._streams.pop(task_id, None)
