"""Unified serial communication service for PromptForge AI.

``SerialService`` is a thin abstraction over ``SerialRuntime``. The runtime
remains the sole owner of pyserial transport, blocking I/O offloading,
connection lifecycle, monitoring, reconnect behavior, and buffers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..runtime.serial_runtime import (
    SerialConfig,
    SerialConnectionState,
    SerialObservation,
    SerialRuntime,
    ObservationSource,
)

__all__ = [
    "SerialConfiguration",
    "SerialConnection",
    "SerialService",
]


@dataclass(frozen=True, slots=True)
class SerialConfiguration:
    """Immutable provider-neutral serial connection configuration."""

    port: str | None = None
    baudrate: int = 115_200
    timeout_s: float = 1.0

    def __post_init__(self) -> None:
        if self.port is not None:
            _validate_port_name(self.port)
            object.__setattr__(self, "port", self.port.strip())
        if (
            not isinstance(self.baudrate, int)
            or isinstance(self.baudrate, bool)
            or self.baudrate <= 0
        ):
            raise ValueError("baudrate must be a positive integer")
        if (
            not isinstance(self.timeout_s, (int, float))
            or isinstance(self.timeout_s, bool)
            or self.timeout_s <= 0
        ):
            raise ValueError("timeout_s must be greater than zero")
        object.__setattr__(self, "timeout_s", float(self.timeout_s))

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SerialConfiguration:
        values = _exact_schema(
            data,
            expected={"port", "baudrate", "timeout_s"},
            label="serial configuration",
        )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class SerialConnection:
    """Immutable snapshot of the service's current connection state."""

    port: str | None
    connected: bool
    baudrate: int

    def __post_init__(self) -> None:
        if self.port is not None:
            _validate_port_name(self.port)
            object.__setattr__(self, "port", self.port.strip())
        if not isinstance(self.connected, bool):
            raise ValueError("connected must be a boolean")
        if (
            not isinstance(self.baudrate, int)
            or isinstance(self.baudrate, bool)
            or self.baudrate <= 0
        ):
            raise ValueError("baudrate must be a positive integer")
        if self.connected and self.port is None:
            raise ValueError("connected serial connection must include a port")

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "connected": self.connected,
            "baudrate": self.baudrate,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SerialConnection:
        values = _exact_schema(
            data,
            expected={"port", "connected", "baudrate"},
            label="serial connection",
        )
        return cls(**values)


class _SerialRuntimeLike(Protocol):
    state: SerialConnectionState
    active_port: str | None
    metrics: Any

    async def connect(self) -> str: ...

    async def disconnect(self) -> None: ...

    async def read(self, size: int = 1) -> bytes: ...

    async def write(self, data: bytes) -> int: ...

    async def read_until(
        self,
        expected: bytes = b"\n",
        size: int | None = None,
    ) -> bytes: ...

    def monitor(self) -> AsyncIterator[SerialObservation]: ...


class SerialService:
    """Delegate serial lifecycle and byte I/O to ``SerialRuntime``."""

    def __init__(
        self,
        configuration: SerialConfiguration,
        *,
        runtime: _SerialRuntimeLike | None = None,
        runtime_config: SerialConfig | None = None,
    ) -> None:
        if not isinstance(configuration, SerialConfiguration):
            raise ValueError("configuration must be a SerialConfiguration")
        if runtime is not None and runtime_config is not None:
            raise ValueError("runtime and runtime_config are mutually exclusive")
        self._configuration = configuration
        self._runtime: _SerialRuntimeLike = runtime or SerialRuntime(
            runtime_config
            or SerialConfig(
                port=configuration.port,
                baudrate=configuration.baudrate,
                read_timeout=configuration.timeout_s,
                connect_timeout=configuration.timeout_s,
            )
        )
        self._monitor_subscribers: set[asyncio.Queue[SerialObservation | None]] = set()
        self._monitor_pump_task: asyncio.Task[None] | None = None

    @property
    def configuration(self) -> SerialConfiguration:
        return self._configuration

    @property
    def state(self) -> SerialConnectionState:
        return self._runtime.state

    @property
    def active_port(self) -> str | None:
        return self._runtime.active_port

    @property
    def metrics(self) -> Any:
        return self._runtime.metrics

    async def connect(self) -> SerialConnection:
        """Open the configured port and return a connected state snapshot."""

        if self.is_connected():
            return self._connection_snapshot()
        port = await self._runtime.connect()
        return SerialConnection(
            port=port,
            connected=True,
            baudrate=self._configuration.baudrate,
        )

    async def disconnect(self) -> SerialConnection:
        """Close the port idempotently and return a disconnected snapshot."""

        prior_port = self._runtime.active_port or self._configuration.port
        await self._runtime.disconnect()
        return SerialConnection(
            port=prior_port,
            connected=False,
            baudrate=self._configuration.baudrate,
        )

    async def read(self, size: int = 1) -> bytes:
        """Read up to ``size`` bytes through the serial runtime."""

        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("size must be a positive integer")
        return await self._runtime.read(size)

    async def write(
        self,
        data: bytes | bytearray | memoryview | str,
    ) -> int:
        """Write bytes or UTF-8 text through the serial runtime."""

        if isinstance(data, str):
            if not data:
                raise ValueError("data must be non-empty")
            payload = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray, memoryview)):
            payload = bytes(data)
            if not payload:
                raise ValueError("data must be non-empty")
        else:
            raise ValueError("data must be bytes-like or a string")
        return await self._runtime.write(payload)

    async def read_until(
        self,
        delimiter: bytes | str = b"\n",
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read through ``delimiter`` with a bounded optional byte limit."""

        if isinstance(delimiter, str):
            if not delimiter:
                raise ValueError("delimiter must be non-empty")
            expected = delimiter.encode("utf-8")
        elif isinstance(delimiter, bytes):
            if not delimiter:
                raise ValueError("delimiter must be non-empty")
            expected = delimiter
        else:
            raise ValueError("delimiter must be bytes or a string")
        if max_bytes is not None and (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or max_bytes <= 0
        ):
            raise ValueError("max_bytes must be a positive integer or None")
        return await self._runtime.read_until(expected, max_bytes)

    def is_connected(self) -> bool:
        """Return whether the runtime currently owns an open port."""

        return (
            self._runtime.state is SerialConnectionState.CONNECTED
            and self._runtime.active_port is not None
        )

    def monitor(self) -> AsyncIterator[SerialObservation]:
        """Subscribe to the service-owned runtime monitor fan-out."""

        return self._monitor_subscription()

    async def _monitor_subscription(self) -> AsyncIterator[SerialObservation]:
        queue: asyncio.Queue[SerialObservation | None] = asyncio.Queue(maxsize=500)
        self._monitor_subscribers.add(queue)
        task = self._monitor_pump_task
        if task is None or task.done():
            self._monitor_pump_task = asyncio.create_task(
                self._pump_monitor(),
                name="forgex-serial-service-monitor",
            )
        try:
            while True:
                observation = await queue.get()
                if observation is None:
                    return
                yield observation
        finally:
            self._monitor_subscribers.discard(queue)

    async def _pump_monitor(self) -> None:
        try:
            async for observation in self._runtime.monitor():
                for queue in tuple(self._monitor_subscribers):
                    dropped = 0
                    while queue.qsize() > queue.maxsize - 2:
                        try:
                            queue.get_nowait()
                            dropped += 1
                        except asyncio.QueueEmpty:
                            break
                    if dropped:
                        queue.put_nowait(SerialObservation(
                            timestamp=time.monotonic(),
                            line=f"[OVERFLOW: {dropped} observations dropped for a slow serial subscriber]",
                            source=ObservationSource.OVERFLOW,
                            port=self.active_port or "",
                            metadata={"dropped": dropped, "scope": "subscriber"},
                        ))
                    try:
                        queue.put_nowait(observation)
                    except asyncio.QueueFull:
                        pass
        finally:
            if self._monitor_pump_task is asyncio.current_task():
                self._monitor_pump_task = None
            for queue in tuple(self._monitor_subscribers):
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(None)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass

    @staticmethod
    def is_port_available(
        port: str,
        available_ports: Sequence[object],
    ) -> bool:
        """Validate a port name against enumerated port records.

        Records may be pyserial objects or mappings containing ``device`` or
        ``port``. Enumeration remains owned by the caller/runtime.
        """

        _validate_port_name(port)
        normalized = port.strip().casefold()
        for item in available_ports:
            if isinstance(item, Mapping):
                candidate = item.get("device", item.get("port", ""))
            else:
                candidate = getattr(item, "device", getattr(item, "port", ""))
            if str(candidate).strip().casefold() == normalized:
                return True
        return False

    def _connection_snapshot(self) -> SerialConnection:
        return SerialConnection(
            port=self._runtime.active_port,
            connected=self.is_connected(),
            baudrate=self._configuration.baudrate,
        )


def _validate_port_name(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("port must be a string")
    if not value.strip():
        raise ValueError("port must be non-empty")
    if "\x00" in value:
        raise ValueError("port cannot contain NUL characters")


def _exact_schema(
    data: Mapping[str, Any],
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(data)
