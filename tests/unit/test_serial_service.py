from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from backend.runtime.serial_runtime import (
    SerialConfig,
    SerialConnectionError,
    SerialConnectionState,
    ObservationSource,
    SerialObservation,
    SerialRuntime,
    SerialRuntimeAlreadyRunningError,
)
from backend.services.serial_service import (
    SerialConfiguration,
    SerialConnection,
    SerialService,
)


class FakeRuntime:
    def __init__(self, *, port: str = "COM7") -> None:
        self.state = SerialConnectionState.DISCONNECTED
        self.active_port: str | None = None
        self.metrics = {"reads": 0}
        self.port = port
        self.connect_count = 0
        self.disconnect_count = 0
        self.read_calls: list[int] = []
        self.write_calls: list[bytes] = []
        self.read_until_calls: list[tuple[bytes, int | None]] = []

    async def connect(self) -> str:
        self.connect_count += 1
        self.state = SerialConnectionState.CONNECTED
        self.active_port = self.port
        return self.port

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self.state = SerialConnectionState.DISCONNECTED
        self.active_port = None

    async def read(self, size: int = 1) -> bytes:
        self.read_calls.append(size)
        return b"data"[:size]

    async def write(self, data: bytes) -> int:
        self.write_calls.append(data)
        return len(data)

    async def read_until(
        self,
        expected: bytes = b"\n",
        size: int | None = None,
    ) -> bytes:
        self.read_until_calls.append((expected, size))
        return b"ready" + expected

    async def monitor(self) -> AsyncIterator[SerialObservation]:
        if False:
            yield SerialObservation(0, "", None, "")  # type: ignore[arg-type]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_configuration_defaults_immutability_and_round_trip() -> None:
    configuration = SerialConfiguration(port=" COM7 ")

    assert configuration.port == "COM7"
    assert configuration.baudrate == 115200
    assert configuration.timeout_s == 1.0
    assert SerialConfiguration.from_dict(configuration.to_dict()) == configuration
    with pytest.raises(FrozenInstanceError):
        configuration.port = "COM8"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": ""},
        {"port": "bad\x00port"},
        {"port": 7},
        {"baudrate": 0},
        {"baudrate": True},
        {"timeout_s": 0},
        {"timeout_s": True},
    ],
)
def test_configuration_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SerialConfiguration(**kwargs)  # type: ignore[arg-type]


def test_connection_immutability_validation_and_round_trip() -> None:
    connection = SerialConnection("COM7", True, 57600)

    assert SerialConnection.from_dict(connection.to_dict()) == connection
    with pytest.raises(FrozenInstanceError):
        connection.connected = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="include a port"):
        SerialConnection(None, True, 115200)


def test_service_builds_runtime_from_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, SerialConfig] = {}
    fake = FakeRuntime()

    def factory(config: SerialConfig) -> FakeRuntime:
        captured["config"] = config
        return fake

    monkeypatch.setattr("backend.services.serial_service.SerialRuntime", factory)
    configuration = SerialConfiguration("COM7", 57600, 2.5)

    service = SerialService(configuration)

    assert service.active_port is None
    assert captured["config"].port == "COM7"
    assert captured["config"].baudrate == 57600
    assert captured["config"].read_timeout == 2.5
    assert captured["config"].connect_timeout == 2.5


def test_connect_disconnect_and_status_are_delegated() -> None:
    runtime = FakeRuntime()
    service = SerialService(SerialConfiguration("COM7"), runtime=runtime)

    assert service.is_connected() is False
    connected = run(service.connect())
    assert connected == SerialConnection("COM7", True, 115200)
    assert service.is_connected() is True
    assert service.active_port == "COM7"
    assert service.state is SerialConnectionState.CONNECTED
    assert service.metrics == {"reads": 0}

    disconnected = run(service.disconnect())
    assert disconnected == SerialConnection("COM7", False, 115200)
    assert service.is_connected() is False
    assert runtime.disconnect_count == 1


def test_connect_is_idempotent_at_service_boundary() -> None:
    runtime = FakeRuntime()
    service = SerialService(SerialConfiguration("COM7"), runtime=runtime)

    first = run(service.connect())
    second = run(service.connect())

    assert first == second
    assert runtime.connect_count == 1


def test_read_write_and_read_until_delegate_without_transport_logic() -> None:
    runtime = FakeRuntime()
    service = SerialService(SerialConfiguration("COM7"), runtime=runtime)

    assert run(service.read(3)) == b"dat"
    assert run(service.write(b"abc")) == 3
    assert run(service.write(bytearray(b"de"))) == 2
    assert run(service.write("ok")) == 2
    assert run(service.read_until("END", max_bytes=64)) == b"readyEND"

    assert runtime.read_calls == [3]
    assert runtime.write_calls == [b"abc", b"de", b"ok"]
    assert runtime.read_until_calls == [(b"END", 64)]


@pytest.mark.parametrize("size", [0, -1, True, 1.5])
def test_read_rejects_invalid_size(size: object) -> None:
    service = SerialService(SerialConfiguration("COM7"), runtime=FakeRuntime())
    with pytest.raises(ValueError, match="size"):
        run(service.read(size))  # type: ignore[arg-type]


@pytest.mark.parametrize("data", [b"", "", None, 123])
def test_write_rejects_invalid_data(data: object) -> None:
    service = SerialService(SerialConfiguration("COM7"), runtime=FakeRuntime())
    with pytest.raises(ValueError, match="data"):
        run(service.write(data))  # type: ignore[arg-type]


@pytest.mark.parametrize("delimiter", [b"", "", None, 1])
def test_read_until_rejects_invalid_delimiter(delimiter: object) -> None:
    service = SerialService(SerialConfiguration("COM7"), runtime=FakeRuntime())
    with pytest.raises(ValueError, match="delimiter"):
        run(service.read_until(delimiter))  # type: ignore[arg-type]


def test_port_availability_supports_objects_and_mappings() -> None:
    ports = [SimpleNamespace(device="COM7"), {"port": "/dev/ttyUSB0"}]

    assert SerialService.is_port_available(" com7 ", ports) is True
    assert SerialService.is_port_available("/dev/ttyUSB0", ports) is True
    assert SerialService.is_port_available("COM8", ports) is False


def test_monitor_fans_out_one_runtime_consumer_to_multiple_subscribers() -> None:
    class StreamingRuntime(FakeRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.gate = asyncio.Event()
            self.monitor_calls = 0

        async def monitor(self) -> AsyncIterator[SerialObservation]:
            self.monitor_calls += 1
            await self.gate.wait()
            yield SerialObservation(1.0, "ready", ObservationSource.DEVICE, "COM7")

    async def scenario() -> tuple[int, str, str]:
        runtime = StreamingRuntime()
        runtime.state = SerialConnectionState.CONNECTED
        runtime.active_port = "COM7"
        service = SerialService(SerialConfiguration("COM7"), runtime=runtime)
        first = service.monitor()
        second = service.monitor()
        first_task = asyncio.create_task(anext(first))
        second_task = asyncio.create_task(anext(second))
        await asyncio.sleep(0)
        runtime.gate.set()
        one, two = await asyncio.gather(first_task, second_task)
        await first.aclose()
        await second.aclose()
        return runtime.monitor_calls, one.line, two.line

    assert asyncio.run(scenario()) == (1, "ready", "ready")


def test_service_rejects_conflicting_runtime_inputs() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        SerialService(
            SerialConfiguration("COM7"),
            runtime=FakeRuntime(),
            runtime_config=SerialConfig(port="COM7"),
        )


class FakePort:
    def __init__(self) -> None:
        self.is_open = True
        self.read_calls: list[int] = []
        self.write_calls: list[bytes] = []
        self.read_until_calls: list[tuple[bytes, int]] = []
        self.flush_count = 0

    def read(self, size: int) -> bytes:
        self.read_calls.append(size)
        return b"value"[:size]

    def write(self, data: bytes) -> int:
        self.write_calls.append(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1

    def read_until(self, *, expected: bytes, size: int) -> bytes:
        self.read_until_calls.append((expected, size))
        return b"line" + expected


def connected_runtime() -> tuple[SerialRuntime, FakePort]:
    runtime = SerialRuntime(SerialConfig(port="COM7", max_line_length=128))
    port = FakePort()
    runtime._port = port  # type: ignore[assignment]
    runtime._active_port_name = "COM7"
    runtime._state = SerialConnectionState.CONNECTED
    return runtime, port


def test_runtime_raw_io_uses_existing_port_handle() -> None:
    async def scenario() -> None:
        runtime, port = connected_runtime()

        assert await runtime.read(3) == b"val"
        assert await runtime.write(b"command") == 7
        assert await runtime.read_until(b"!", 32) == b"line!"
        assert port.read_calls == [3]
        assert port.write_calls == [b"command"]
        assert port.flush_count == 1
        assert port.read_until_calls == [(b"!", 32)]

    asyncio.run(scenario())


def test_runtime_raw_read_is_blocked_while_monitor_owns_input() -> None:
    async def scenario() -> None:
        runtime, _ = connected_runtime()
        runtime._monitor_task = asyncio.current_task()

        with pytest.raises(SerialRuntimeAlreadyRunningError):
            await runtime.read(1)
        with pytest.raises(SerialRuntimeAlreadyRunningError):
            await runtime.read_until()

    asyncio.run(scenario())


def test_runtime_raw_io_requires_connection() -> None:
    runtime = SerialRuntime(SerialConfig(port="COM7"))

    with pytest.raises(SerialConnectionError):
        run(runtime.read())
    with pytest.raises(SerialConnectionError):
        run(runtime.write(b"x"))


def test_runtime_read_until_uses_bounded_default() -> None:
    async def scenario() -> None:
        runtime, port = connected_runtime()

        await runtime.read_until()

        assert port.read_until_calls == [(b"\n", 128)]

    asyncio.run(scenario())
