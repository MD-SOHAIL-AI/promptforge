"""Bounded internal-only event fanout and replay transport."""

from __future__ import annotations

import asyncio
import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass

from .models import BridgeEventType, BridgeRunEvent


@dataclass(slots=True)
class BridgeEventSubscription:
    subscription_id: str
    run_id: str
    queue: asyncio.Queue[BridgeRunEvent]
    _transport: "InternalBridgeEventTransport"
    _closed: bool = False

    async def get(self) -> BridgeRunEvent:
        return await self.queue.get()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._transport.unsubscribe(self)


class InternalBridgeEventTransport:
    """No network surface. Slow subscribers are resynchronized, never awaited."""

    def __init__(self, *, history_limit: int = 200, subscriber_queue_size: int = 32) -> None:
        if history_limit < 1 or subscriber_queue_size < 1:
            raise ValueError("Event transport bounds must be positive.")
        self.history_limit = history_limit
        self.subscriber_queue_size = subscriber_queue_size
        self._history: dict[str, deque[BridgeRunEvent]] = defaultdict(lambda: deque(maxlen=self.history_limit))
        self._subscribers: dict[str, dict[str, BridgeEventSubscription]] = defaultdict(dict)
        self._counter = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: BridgeRunEvent) -> None:
        async with self._lock:
            if self._closed:
                return
            history = self._history[event.run_id]
            if history and event.sequence <= history[-1].sequence:
                return
            history.append(event)
            subscribers = tuple(self._subscribers.get(event.run_id, {}).values())
            for subscription in subscribers:
                if subscription._closed:
                    continue
                try:
                    subscription.queue.put_nowait(event)
                except asyncio.QueueFull:
                    while not subscription.queue.empty():
                        try:
                            subscription.queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    gap = _resync_event(event)
                    try:
                        subscription.queue.put_nowait(gap)
                    except asyncio.QueueFull:
                        pass

    async def subscribe(self, run_id: str, *, after_sequence: int = 0) -> BridgeEventSubscription:
        async with self._lock:
            if self._closed:
                raise RuntimeError("Bridge event transport is closed.")
            self._counter += 1
            subscription = BridgeEventSubscription(
                subscription_id=f"bridge-subscription-{self._counter}",
                run_id=run_id,
                queue=asyncio.Queue(maxsize=self.subscriber_queue_size),
                _transport=self,
            )
            self._subscribers[run_id][subscription.subscription_id] = subscription
            for event in self._replay_locked(run_id, after_sequence, self.subscriber_queue_size):
                try:
                    subscription.queue.put_nowait(event)
                except asyncio.QueueFull:
                    break
            return subscription

    async def unsubscribe(self, subscription: BridgeEventSubscription) -> None:
        async with self._lock:
            subscriptions = self._subscribers.get(subscription.run_id)
            if subscriptions is not None:
                subscriptions.pop(subscription.subscription_id, None)
                if not subscriptions:
                    self._subscribers.pop(subscription.run_id, None)

    async def replay(self, run_id: str, *, after_sequence: int = 0, limit: int = 200) -> tuple[BridgeRunEvent, ...]:
        async with self._lock:
            return self._replay_locked(run_id, after_sequence, max(1, min(limit, self.history_limit)))

    async def close(self) -> None:
        """Release every subscriber during backend shutdown."""

        async with self._lock:
            self._closed = True
            for subscriptions in self._subscribers.values():
                for subscription in subscriptions.values():
                    subscription._closed = True
            self._subscribers.clear()
            self._history.clear()

    def _replay_locked(self, run_id: str, after_sequence: int, limit: int) -> tuple[BridgeRunEvent, ...]:
        history = tuple(self._history.get(run_id, ()))
        if not history:
            return ()
        selected = tuple(event for event in history if event.sequence > after_sequence)
        if after_sequence and history[0].sequence > after_sequence + 1:
            return (_resync_event(history[-1]),)
        return selected[:limit]


def _resync_event(reference: BridgeRunEvent) -> BridgeRunEvent:
    digest = hashlib.sha256(f"{reference.run_id}:resync:{reference.sequence}".encode("utf-8")).hexdigest()[:24]
    return BridgeRunEvent(
        event_id=f"event-{digest}",
        run_id=reference.run_id,
        sequence=reference.sequence,
        timestamp=reference.timestamp,
        event_type=BridgeEventType.RESYNC_REQUIRED,
        status=reference.status,
        safe_message="Event history gap detected; replay from persisted run events.",
    )
