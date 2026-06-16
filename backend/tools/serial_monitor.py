"""Serial observation tool built on the frozen PromptForge serial runtime.

``SerialMonitor`` owns observation policy only: bounded log collection,
pattern-based termination, optional live callbacks, and ``ObserveResult``
construction. ``SerialRuntime`` remains the sole owner of serial transport,
connection lifecycle, buffering, and reconnect behavior.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..runtime.result import (
    FailureResult,
    ObserveResult,
    ObserveTerminationReason,
    ResultStatus,
)
from ..runtime.serial_runtime import (
    ObservationSource,
    SerialConfig,
    SerialConnectionState,
    SerialObservation,
    SerialRuntime,
)
from ..services.serial_service import (
    SerialConfiguration,
    SerialConnection,
    SerialService,
)

__all__ = [
    "ObservationCallback",
    "SerialMonitor",
    "SerialMonitorConfig",
]

ObservationCallback = Callable[
    [SerialObservation],
    Optional[Awaitable[None]],
]


class _SerialRuntimeLike(Protocol):
    """Structural runtime interface used for production and test injection."""

    state: SerialConnectionState
    active_port: Optional[str]
    metrics: Any

    async def connect(self) -> str | SerialConnection: ...

    async def disconnect(self) -> None: ...

    def monitor(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class SerialMonitorConfig:
    """Immutable policy for one serial observation window."""

    port: Optional[str] = None
    baudrate: int = 115_200
    timeout_s: float = 30.0
    connect_timeout_s: float = 10.0
    success_pattern: str = ""
    failure_pattern: str = ""
    read_timeout: float = 0.1
    max_line_length: int = 4_096
    max_log_lines: int = 2_000
    drain_on_connect: bool = True

    def __post_init__(self) -> None:
        if self.port is not None and (
            not isinstance(self.port, str)
            or not self.port.strip()
            or "\x00" in self.port
        ):
            raise ValueError("port must be None or a non-empty NUL-free string")
        if not isinstance(self.baudrate, int) or isinstance(self.baudrate, bool):
            raise ValueError("baudrate must be an integer")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be greater than zero")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if self.connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be greater than zero")
        if self.read_timeout <= 0:
            raise ValueError("read_timeout must be greater than zero")
        if self.max_line_length < 64:
            raise ValueError("max_line_length must be at least 64")
        if self.max_log_lines <= 0:
            raise ValueError("max_log_lines must be greater than zero")
        if not isinstance(self.success_pattern, str):
            raise ValueError("success_pattern must be a string")
        if not isinstance(self.failure_pattern, str):
            raise ValueError("failure_pattern must be a string")
        if not isinstance(self.drain_on_connect, bool):
            raise ValueError("drain_on_connect must be a boolean")


@dataclass(frozen=True, slots=True)
class _MonitorOutcome:
    reason: ObserveTerminationReason
    matched_pattern: Optional[str] = None


class SerialMonitor:
    """Connect, stream, collect, and summarize serial observations."""

    def __init__(
        self,
        config: SerialMonitorConfig,
        runtime: Optional[_SerialRuntimeLike] = None,
    ) -> None:
        self._config = config
        if runtime is not None:
            self._runtime = runtime
        else:
            runtime_config = SerialConfig(
                port=config.port.strip() if config.port else None,
                baudrate=config.baudrate,
                read_timeout=config.read_timeout,
                connect_timeout=config.connect_timeout_s,
                max_line_length=config.max_line_length,
                buffer_maxlines=max(10, config.max_log_lines),
                drain_on_connect=config.drain_on_connect,
            )
            self._runtime = SerialService(
                SerialConfiguration(
                    port=runtime_config.port,
                    baudrate=runtime_config.baudrate,
                    timeout_s=runtime_config.read_timeout,
                ),
                runtime=SerialRuntime(runtime_config),
            )

    async def observe(
        self,
        on_observation: Optional[ObservationCallback] = None,
    ) -> ObserveResult:
        """Run one bounded observation window and return its terminal result.

        Every observation is collected before the optional callback is invoked.
        Callback exceptions terminate the operation with ``ERROR``. External
        cancellation is re-raised after transport cleanup.
        """
        started = time.monotonic()
        monitoring_started: Optional[float] = None
        logs: deque[dict[str, Any]] = deque(maxlen=self._config.max_log_lines)
        total_observations = 0
        device_lines = 0
        reconnect_count = 0
        active_port = self._config.port or ""
        outcome: Optional[_MonitorOutcome] = None

        async def consume() -> _MonitorOutcome:
            nonlocal total_observations, device_lines, reconnect_count, active_port
            stream = self._runtime.monitor()
            try:
                async for observation in stream:
                    total_observations += 1
                    logs.append(_observation_dict(observation))
                    if observation.port:
                        active_port = observation.port

                    if observation.source is ObservationSource.DEVICE:
                        device_lines += 1
                    elif (
                        observation.source is ObservationSource.RECONNECT
                        and observation.metadata.get("event") == "reconnected"
                    ):
                        reconnect_count += 1

                    if on_observation is not None:
                        callback_result = on_observation(observation)
                        if inspect.isawaitable(callback_result):
                            await callback_result

                    if observation.source is ObservationSource.DEVICE:
                        if (
                            self._config.failure_pattern
                            and self._config.failure_pattern in observation.line
                        ):
                            return _MonitorOutcome(
                                ObserveTerminationReason.FAILURE_PATTERN,
                                self._config.failure_pattern,
                            )
                        if (
                            self._config.success_pattern
                            and self._config.success_pattern in observation.line
                        ):
                            return _MonitorOutcome(
                                ObserveTerminationReason.SUCCESS_PATTERN,
                                self._config.success_pattern,
                            )

                    if (
                        observation.source is ObservationSource.RECONNECT
                        and observation.metadata.get("event") == "failed"
                    ):
                        return _MonitorOutcome(
                            ObserveTerminationReason.RECONNECT_FAILED
                        )

                if self._runtime.state is SerialConnectionState.FAILED:
                    return _MonitorOutcome(
                        ObserveTerminationReason.RECONNECT_FAILED
                    )
                return _MonitorOutcome(ObserveTerminationReason.ERROR)
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()

        try:
            connection = await self._runtime.connect()
            active_port = (
                connection.port
                if isinstance(connection, SerialConnection)
                else connection
            )
            monitoring_started = time.monotonic()
            try:
                outcome = await asyncio.wait_for(
                    consume(),
                    timeout=self._config.timeout_s,
                )
            except asyncio.TimeoutError:
                outcome = _MonitorOutcome(ObserveTerminationReason.TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                return self._error_result(
                    started=started,
                    monitoring_started=monitoring_started,
                    active_port=active_port,
                    device_lines=device_lines,
                    reconnect_count=reconnect_count,
                    logs=logs,
                    total_observations=total_observations,
                    exc=exc,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._error_result(
                started=started,
                monitoring_started=monitoring_started,
                active_port=active_port,
                device_lines=device_lines,
                reconnect_count=reconnect_count,
                logs=logs,
                total_observations=total_observations,
                exc=exc,
            )
        finally:
            try:
                await self._runtime.disconnect()
            except asyncio.CancelledError:
                # Cleanup must finish before cancellation is allowed to escape.
                await asyncio.shield(self._runtime.disconnect())
                raise
            except Exception:
                # The primary operation result remains authoritative. Runtime
                # disconnect is documented as safe and best-effort.
                pass

        assert outcome is not None
        return self._result_from_outcome(
            outcome=outcome,
            started=started,
            monitoring_started=monitoring_started,
            active_port=active_port,
            device_lines=device_lines,
            reconnect_count=reconnect_count,
            logs=logs,
            total_observations=total_observations,
        )

    def _result_from_outcome(
        self,
        *,
        outcome: _MonitorOutcome,
        started: float,
        monitoring_started: Optional[float],
        active_port: str,
        device_lines: int,
        reconnect_count: int,
        logs: deque[dict[str, Any]],
        total_observations: int,
    ) -> ObserveResult:
        reason = outcome.reason
        failure: Optional[FailureResult] = None

        if reason is ObserveTerminationReason.SUCCESS_PATTERN:
            status = ResultStatus.SUCCESS
            success = True
            message = f"Serial success pattern matched: {outcome.matched_pattern!r}"
        elif reason is ObserveTerminationReason.FAILURE_PATTERN:
            status = ResultStatus.FAILED
            success = False
            message = f"Serial failure pattern matched: {outcome.matched_pattern!r}"
            failure = _unknown_failure(
                message,
                exception_type="FailurePatternMatched",
                raw_output=_device_log_text(logs),
            )
        elif reason is ObserveTerminationReason.RECONNECT_FAILED:
            status = ResultStatus.PARTIAL if device_lines else ResultStatus.FAILED
            success = False
            message = "Serial device reconnect failed"
            failure = _unknown_failure(
                message,
                exception_type="SerialConnectionError",
                raw_output=_device_log_text(logs),
            )
        elif reason is ObserveTerminationReason.TIMEOUT:
            if not self._config.success_pattern:
                status = ResultStatus.SUCCESS
                success = True
                message = "Serial log collection window completed"
            else:
                status = ResultStatus.PARTIAL if device_lines else ResultStatus.TIMEOUT
                success = False
                message = (
                    f"Serial success pattern {self._config.success_pattern!r} "
                    f"was not observed within {self._config.timeout_s:.1f}s"
                )
                failure = _unknown_failure(
                    message,
                    exception_type="TimeoutError",
                    raw_output=_device_log_text(logs),
                )
        else:
            status = ResultStatus.FAILED
            success = False
            message = "Serial monitoring ended unexpectedly"
            failure = _unknown_failure(
                message,
                exception_type="SerialRuntimeError",
                raw_output=_device_log_text(logs),
            )

        return ObserveResult(
            success=success,
            status=status,
            duration_ms=_elapsed_ms(started),
            message=message,
            metadata=_result_metadata(logs, total_observations),
            lines_captured=device_lines,
            reconnect_count=reconnect_count,
            monitoring_duration_ms=_monitoring_ms(monitoring_started),
            termination_reason=reason,
            matched_pattern=outcome.matched_pattern,
            port=active_port,
            failure=failure,
        )

    @staticmethod
    def _error_result(
        *,
        started: float,
        monitoring_started: Optional[float],
        active_port: str,
        device_lines: int,
        reconnect_count: int,
        logs: deque[dict[str, Any]],
        total_observations: int,
        exc: BaseException,
    ) -> ObserveResult:
        message = f"Serial monitoring failed: {exc}"
        return ObserveResult(
            success=False,
            status=ResultStatus.FAILED,
            duration_ms=_elapsed_ms(started),
            message=message,
            metadata=_result_metadata(logs, total_observations),
            lines_captured=device_lines,
            reconnect_count=reconnect_count,
            monitoring_duration_ms=_monitoring_ms(monitoring_started),
            termination_reason=ObserveTerminationReason.ERROR,
            port=active_port,
            failure=_unknown_failure(
                message,
                exception_type=type(exc).__name__,
                raw_output=str(exc),
            ),
        )


def _observation_dict(observation: SerialObservation) -> dict[str, Any]:
    return {
        "timestamp": observation.timestamp,
        "line": observation.line,
        "source": observation.source.value,
        "port": observation.port,
        "metadata": dict(observation.metadata),
    }


def _result_metadata(
    logs: deque[dict[str, Any]],
    total_observations: int,
) -> dict[str, Any]:
    retained = list(logs)
    return {
        "observations": retained,
        "observations_total": total_observations,
        "observations_dropped": max(0, total_observations - len(retained)),
    }


def _device_log_text(logs: deque[dict[str, Any]]) -> str:
    return "\n".join(
        str(item["line"])
        for item in logs
        if item["source"] == ObservationSource.DEVICE.value
    )


def _unknown_failure(
    message: str,
    *,
    exception_type: str,
    raw_output: str,
) -> FailureResult:
    return FailureResult(
        category="UNKNOWN",
        message=message,
        retryable=False,
        stage="observe",
        exception_type=exception_type,
        raw_output=raw_output,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _monitoring_ms(started: Optional[float]) -> int:
    return 0 if started is None else _elapsed_ms(started)
