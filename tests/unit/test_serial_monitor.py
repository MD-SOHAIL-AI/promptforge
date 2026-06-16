from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Optional

import pytest

from backend.runtime.result import (
    ObserveTerminationReason,
    ResultStatus,
)
from backend.runtime.serial_runtime import (
    ObservationSource,
    SerialConnectionState,
    SerialObservation,
)
from backend.tools.serial_monitor import SerialMonitor, SerialMonitorConfig


def observation(
    line: str,
    *,
    source: ObservationSource = ObservationSource.DEVICE,
    port: str = "COM7",
    metadata: Optional[dict] = None,
) -> SerialObservation:
    return SerialObservation(
        timestamp=123.5,
        line=line,
        source=source,
        port=port,
        metadata=metadata or {},
    )


class FakeRuntime:
    def __init__(
        self,
        observations: tuple[SerialObservation, ...] = (),
        *,
        connect_error: Optional[BaseException] = None,
        monitor_error: Optional[BaseException] = None,
        block: bool = False,
        active_port: str = "COM7",
    ) -> None:
        self.observations = observations
        self.connect_error = connect_error
        self.monitor_error = monitor_error
        self.block = block
        self.state = SerialConnectionState.DISCONNECTED
        self.active_port: Optional[str] = None
        self.connected_port = active_port
        self.metrics = object()
        self.connect_count = 0
        self.disconnect_count = 0
        self.monitor_closed = False

    async def connect(self) -> str:
        self.connect_count += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.state = SerialConnectionState.CONNECTED
        self.active_port = self.connected_port
        return self.connected_port

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self.state = SerialConnectionState.DISCONNECTED
        self.active_port = None

    async def monitor(self) -> AsyncIterator[SerialObservation]:
        try:
            for item in self.observations:
                if (
                    item.source is ObservationSource.RECONNECT
                    and item.metadata.get("event") == "failed"
                ):
                    self.state = SerialConnectionState.FAILED
                elif (
                    item.source is ObservationSource.RECONNECT
                    and item.metadata.get("event") == "reconnected"
                ):
                    self.state = SerialConnectionState.CONNECTED
                    self.active_port = item.port
                yield item

            if self.monitor_error is not None:
                raise self.monitor_error

            while self.block:
                await asyncio.sleep(3600)
        finally:
            self.monitor_closed = True


def run_observe(
    runtime: FakeRuntime,
    config: Optional[SerialMonitorConfig] = None,
    callback=None,
):
    return asyncio.run(
        SerialMonitor(
            config or SerialMonitorConfig(port="COM7"),
            runtime=runtime,
        ).observe(callback)
    )


def test_success_pattern_returns_success_and_collected_logs() -> None:
    runtime = FakeRuntime(
        (
            observation(
                "monitoring started",
                source=ObservationSource.RUNTIME,
            ),
            observation("booting"),
            observation("READY: application started"),
            observation("not consumed"),
        )
    )

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", success_pattern="READY"),
    )

    assert result.success is True
    assert result.status is ResultStatus.SUCCESS
    assert result.termination_reason is ObserveTerminationReason.SUCCESS_PATTERN
    assert result.matched_pattern == "READY"
    assert result.lines_captured == 2
    assert [item["line"] for item in result.metadata["observations"]] == [
        "monitoring started",
        "booting",
        "READY: application started",
    ]
    assert runtime.disconnect_count == 1
    assert runtime.monitor_closed is True


def test_failure_pattern_returns_unclassified_failure() -> None:
    runtime = FakeRuntime(
        (observation("boot"), observation("FATAL: sensor unavailable"))
    )

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", failure_pattern="FATAL"),
    )

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.termination_reason is ObserveTerminationReason.FAILURE_PATTERN
    assert result.matched_pattern == "FATAL"
    assert result.failure is not None
    assert result.failure.category == "UNKNOWN"
    assert result.failure.stage == "observe"
    assert result.failure.exception_type == "FailurePatternMatched"
    assert "sensor unavailable" in (result.failure.raw_output or "")


def test_failure_pattern_takes_precedence_on_same_line() -> None:
    runtime = FakeRuntime((observation("READY but FATAL"),))

    result = run_observe(
        runtime,
        SerialMonitorConfig(
            port="COM7",
            success_pattern="READY",
            failure_pattern="FATAL",
        ),
    )

    assert result.termination_reason is ObserveTerminationReason.FAILURE_PATTERN


def test_patterns_are_case_sensitive_literal_substrings() -> None:
    runtime = FakeRuntime((observation("ready"),), block=True)

    result = run_observe(
        runtime,
        SerialMonitorConfig(
            port="COM7",
            timeout_s=0.01,
            success_pattern="READY",
        ),
    )

    assert result.success is False
    assert result.status is ResultStatus.PARTIAL
    assert result.termination_reason is ObserveTerminationReason.TIMEOUT


def test_log_only_window_timeout_is_successful_collection() -> None:
    runtime = FakeRuntime((observation("one"), observation("two")), block=True)

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", timeout_s=0.01),
    )

    assert result.success is True
    assert result.status is ResultStatus.SUCCESS
    assert result.termination_reason is ObserveTerminationReason.TIMEOUT
    assert result.lines_captured == 2
    assert result.failure is None
    assert "collection window completed" in result.message


def test_awaited_success_timeout_without_lines_is_timeout() -> None:
    runtime = FakeRuntime(block=True)

    result = run_observe(
        runtime,
        SerialMonitorConfig(
            port="COM7",
            timeout_s=0.01,
            success_pattern="READY",
        ),
    )

    assert result.success is False
    assert result.status is ResultStatus.TIMEOUT
    assert result.lines_captured == 0
    assert result.failure is not None
    assert result.failure.category == "UNKNOWN"
    assert result.failure.exception_type == "TimeoutError"


def test_awaited_success_timeout_with_lines_is_partial() -> None:
    runtime = FakeRuntime((observation("still booting"),), block=True)

    result = run_observe(
        runtime,
        SerialMonitorConfig(
            port="COM7",
            timeout_s=0.01,
            success_pattern="READY",
        ),
    )

    assert result.success is False
    assert result.status is ResultStatus.PARTIAL
    assert result.lines_captured == 1


def test_sync_callback_receives_every_observation_until_termination() -> None:
    runtime = FakeRuntime(
        (
            observation("started", source=ObservationSource.RUNTIME),
            observation("READY"),
        )
    )
    received: list[str] = []

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", success_pattern="READY"),
        lambda item: received.append(item.line),
    )

    assert result.success is True
    assert received == ["started", "READY"]


def test_async_callback_is_awaited() -> None:
    runtime = FakeRuntime((observation("READY"),))
    received: list[str] = []

    async def callback(item: SerialObservation) -> None:
        await asyncio.sleep(0)
        received.append(item.line)

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", success_pattern="READY"),
        callback,
    )

    assert result.success is True
    assert received == ["READY"]


def test_callback_error_returns_error_and_disconnects() -> None:
    runtime = FakeRuntime((observation("line"),))

    def callback(item: SerialObservation) -> None:
        raise RuntimeError("consumer failed")

    result = run_observe(runtime, callback=callback)

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.termination_reason is ObserveTerminationReason.ERROR
    assert result.failure is not None
    assert result.failure.exception_type == "RuntimeError"
    assert "consumer failed" in result.message
    assert runtime.disconnect_count == 1
    assert runtime.monitor_closed is True


def test_bounded_logs_report_dropped_observation_count() -> None:
    runtime = FakeRuntime(
        tuple(observation(f"line-{index}") for index in range(5)),
        block=True,
    )

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", timeout_s=0.01, max_log_lines=3),
    )

    assert result.lines_captured == 5
    assert result.metadata["observations_total"] == 5
    assert result.metadata["observations_dropped"] == 2
    assert [item["line"] for item in result.metadata["observations"]] == [
        "line-2",
        "line-3",
        "line-4",
    ]


def test_only_device_observations_count_as_captured_lines() -> None:
    runtime = FakeRuntime(
        (
            observation("runtime", source=ObservationSource.RUNTIME),
            observation("overflow", source=ObservationSource.OVERFLOW),
            observation("device"),
            observation("READY"),
        )
    )

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", success_pattern="READY"),
    )

    assert result.lines_captured == 2
    assert result.metadata["observations_total"] == 4


def test_reconnect_success_updates_count_and_final_port() -> None:
    runtime = FakeRuntime(
        (
            observation(
                "disconnect",
                source=ObservationSource.RECONNECT,
                port="COM7",
                metadata={"event": "disconnect"},
            ),
            observation(
                "reconnected",
                source=ObservationSource.RECONNECT,
                port="COM9",
                metadata={"event": "reconnected"},
            ),
            observation("READY", port="COM9"),
        )
    )

    result = run_observe(
        runtime,
        SerialMonitorConfig(port="COM7", success_pattern="READY"),
    )

    assert result.success is True
    assert result.reconnect_count == 1
    assert result.port == "COM9"


@pytest.mark.parametrize(
    ("device_lines", "expected_status"),
    [(0, ResultStatus.FAILED), (1, ResultStatus.PARTIAL)],
)
def test_reconnect_exhaustion_maps_to_terminal_result(
    device_lines: int,
    expected_status: ResultStatus,
) -> None:
    observations = [observation("log")] if device_lines else []
    observations.append(
        observation(
            "Reconnect failed",
            source=ObservationSource.RECONNECT,
            metadata={"event": "failed", "attempts": 3},
        )
    )
    runtime = FakeRuntime(tuple(observations))

    result = run_observe(runtime)

    assert result.success is False
    assert result.status is expected_status
    assert result.termination_reason is ObserveTerminationReason.RECONNECT_FAILED
    assert result.failure is not None
    assert result.failure.category == "UNKNOWN"


def test_connection_error_returns_error_result_and_disconnects() -> None:
    runtime = FakeRuntime(connect_error=PermissionError("port denied"))

    result = run_observe(runtime)

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.termination_reason is ObserveTerminationReason.ERROR
    assert result.monitoring_duration_ms == 0
    assert result.failure is not None
    assert result.failure.exception_type == "PermissionError"
    assert runtime.disconnect_count == 1


def test_monitor_error_returns_error_with_collected_logs() -> None:
    runtime = FakeRuntime(
        (observation("before failure"),),
        monitor_error=OSError("device vanished"),
    )

    result = run_observe(runtime)

    assert result.success is False
    assert result.termination_reason is ObserveTerminationReason.ERROR
    assert result.lines_captured == 1
    assert result.metadata["observations"][0]["line"] == "before failure"
    assert runtime.disconnect_count == 1


def test_stream_ending_without_reason_is_error() -> None:
    runtime = FakeRuntime((observation("one line"),))

    result = run_observe(runtime)

    assert result.success is False
    assert result.termination_reason is ObserveTerminationReason.ERROR
    assert result.failure is not None


def test_external_cancellation_propagates_after_cleanup() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime(block=True)
        monitor = SerialMonitor(
            SerialMonitorConfig(port="COM7", timeout_s=60),
            runtime=runtime,
        )
        task = asyncio.create_task(monitor.observe())
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert runtime.disconnect_count == 1
        assert runtime.monitor_closed is True

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": ""},
        {"port": "COM\x007"},
        {"baudrate": 0},
        {"baudrate": True},
        {"timeout_s": 0},
        {"connect_timeout_s": 0},
        {"read_timeout": 0},
        {"max_line_length": 63},
        {"max_log_lines": 0},
        {"success_pattern": None},
        {"failure_pattern": None},
        {"drain_on_connect": 1},
    ],
)
def test_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SerialMonitorConfig(**kwargs)  # type: ignore[arg-type]


def test_default_runtime_receives_serial_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}
    fake = FakeRuntime()

    def runtime_factory(config):
        captured["config"] = config
        return fake

    monkeypatch.setattr(
        "backend.tools.serial_monitor.SerialRuntime",
        runtime_factory,
    )
    config = SerialMonitorConfig(
        port=" COM7 ",
        baudrate=57600,
        connect_timeout_s=4,
        read_timeout=0.2,
        max_line_length=512,
        max_log_lines=3,
        drain_on_connect=False,
    )

    SerialMonitor(config)

    serial_config = captured["config"]
    assert serial_config.port == "COM7"
    assert serial_config.baudrate == 57600
    assert serial_config.connect_timeout == 4
    assert serial_config.read_timeout == 0.2
    assert serial_config.max_line_length == 512
    assert serial_config.buffer_maxlines == 10
    assert serial_config.drain_on_connect is False
