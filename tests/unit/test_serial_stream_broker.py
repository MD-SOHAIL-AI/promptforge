from __future__ import annotations

import asyncio

from backend.runtime.serial_runtime import ObservationSource, SerialObservation
from backend.services.serial_stream_broker import SerialStreamBroker


class FakeSerialService:
    def __init__(self, lines: tuple[str, ...]) -> None:
        self.lines = lines
        self.monitor_calls = 0

    async def monitor(self):
        self.monitor_calls += 1
        for line in self.lines:
            yield SerialObservation(1.0, line, ObservationSource.DEVICE, "COM7")


def test_serial_stream_broker_consumes_runtime_once_and_replays() -> None:
    async def run() -> tuple[int, list[str], list[str]]:
        broker = SerialStreamBroker(max_events=10)
        service = FakeSerialService(("one", "two"))
        _, live = await broker.subscribe()
        await broker.attach(service)  # type: ignore[arg-type]
        first = await asyncio.wait_for(live.get(), timeout=1)
        second = await asyncio.wait_for(live.get(), timeout=1)
        replay, replay_queue = await broker.subscribe(after=first.sequence)
        await broker.unsubscribe(live)
        await broker.unsubscribe(replay_queue)
        await broker.detach()
        return service.monitor_calls, [first.observation.line, second.observation.line], [item.observation.line for item in replay]

    assert asyncio.run(run()) == (1, ["one", "two"], ["two"])
