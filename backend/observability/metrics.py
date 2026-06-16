"""Lightweight, dependency-free runtime metrics for PromptForge.

Durations are recorded in milliseconds, matching PromptForge runtime result
contracts.  ``last_execution_time`` is a Unix timestamp in seconds so metric
snapshots are directly JSON-compatible.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Final, TypedDict

__all__ = [
    "MetricCounter",
    "MetricSnapshot",
    "MetricTimer",
    "MetricsRegistry",
    "MetricsSnapshot",
]


class MetricSnapshot(TypedDict):
    """JSON-compatible snapshot for one PromptForge operation type."""

    success_count: int
    failure_count: int
    average_duration: float
    last_execution_time: float | None


MetricsSnapshot = dict[str, MetricSnapshot]

_METRIC_NAMES: Final[tuple[str, ...]] = (
    "executions",
    "generations",
    "builds",
    "flashes",
    "monitor_sessions",
)


class MetricCounter:
    """A non-negative, thread-safe integer counter."""

    def __init__(self, initial_value: int = 0) -> None:
        _validate_count(initial_value, "initial_value")
        self._value = initial_value
        self._lock = threading.Lock()

    def increment(self, amount: int = 1) -> int:
        """Increase the counter and return its new value."""

        _validate_count(amount, "amount")
        with self._lock:
            self._value += amount
            return self._value

    @property
    def value(self) -> int:
        """Return the current counter value."""

        with self._lock:
            return self._value

    def reset(self) -> None:
        """Reset the counter to zero."""

        with self._lock:
            self._value = 0


class MetricTimer:
    """Track count, total duration, average, and latest record time."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock or time.time
        self._count = 0
        self._total_duration = 0.0
        self._last_execution_time: float | None = None
        self._lock = threading.Lock()

    def record(
        self,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one non-negative duration in milliseconds.

        ``execution_time`` may be supplied for deterministic replay.  When it
        is omitted, the timer's clock supplies the Unix timestamp.
        """

        duration_value = _validate_number(duration, "duration", minimum=0.0)
        timestamp = self._timestamp(execution_time)
        with self._lock:
            new_total = self._total_duration + duration_value
            if not math.isfinite(new_total):
                raise ValueError("total duration exceeds the finite range")
            self._count += 1
            self._total_duration = new_total
            self._last_execution_time = timestamp

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    @property
    def total_duration(self) -> float:
        with self._lock:
            return self._total_duration

    @property
    def average_duration(self) -> float:
        with self._lock:
            if self._count == 0:
                return 0.0
            return self._total_duration / self._count

    @property
    def last_execution_time(self) -> float | None:
        with self._lock:
            return self._last_execution_time

    def snapshot(self) -> tuple[int, float, float | None]:
        """Return count, average duration, and latest execution timestamp."""

        with self._lock:
            average = (
                0.0
                if self._count == 0
                else self._total_duration / self._count
            )
            return self._count, average, self._last_execution_time

    def reset(self) -> None:
        """Clear all recorded timing data."""

        with self._lock:
            self._count = 0
            self._total_duration = 0.0
            self._last_execution_time = None

    def _timestamp(self, execution_time: float | None) -> float:
        raw_value = self._clock() if execution_time is None else execution_time
        return _validate_number(
            raw_value,
            "execution_time",
            minimum=0.0,
        )


class _OperationMetrics:
    """Mutable metric components for one registry operation."""

    __slots__ = ("failure_count", "success_count", "timer")

    def __init__(self, clock: Callable[[], float]) -> None:
        self.success_count = MetricCounter()
        self.failure_count = MetricCounter()
        self.timer = MetricTimer(clock=clock)

    def record(
        self,
        success: bool,
        duration: float,
        execution_time: float | None,
    ) -> None:
        self.timer.record(duration, execution_time=execution_time)
        if success:
            self.success_count.increment()
        else:
            self.failure_count.increment()

    def snapshot(self) -> MetricSnapshot:
        _, average, last_execution_time = self.timer.snapshot()
        return {
            "success_count": self.success_count.value,
            "failure_count": self.failure_count.value,
            "average_duration": average,
            "last_execution_time": last_execution_time,
        }

    def reset(self) -> None:
        self.success_count.reset()
        self.failure_count.reset()
        self.timer.reset()


class MetricsRegistry:
    """Thread-safe in-memory registry for PromptForge runtime operations."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        selected_clock = clock or time.time
        self._metrics: Mapping[str, _OperationMetrics] = MappingProxyType(
            {
                name: _OperationMetrics(selected_clock)
                for name in _METRIC_NAMES
            }
        )
        self._lock = threading.RLock()

    def record_execution(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one complete workflow execution."""

        self._record("executions", success, duration, execution_time)

    def record_generation(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one source-generation operation."""

        self._record("generations", success, duration, execution_time)

    def record_build(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one firmware build."""

        self._record("builds", success, duration, execution_time)

    def record_flash(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one firmware flash attempt."""

        self._record("flashes", success, duration, execution_time)

    def record_monitor_session(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Record one serial monitor session."""

        self._record("monitor_sessions", success, duration, execution_time)

    def record_monitor(
        self,
        success: bool,
        duration: float,
        *,
        execution_time: float | None = None,
    ) -> None:
        """Alias for :meth:`record_monitor_session`."""

        self.record_monitor_session(
            success,
            duration,
            execution_time=execution_time,
        )

    def get_metrics(self) -> MetricsSnapshot:
        """Return a detached, deterministic snapshot of all metrics."""

        with self._lock:
            return {
                name: self._metrics[name].snapshot()
                for name in _METRIC_NAMES
            }

    def reset_metrics(self) -> None:
        """Atomically reset every registered operation metric."""

        with self._lock:
            for name in _METRIC_NAMES:
                self._metrics[name].reset()

    def _record(
        self,
        metric_name: str,
        success: bool,
        duration: float,
        execution_time: float | None,
    ) -> None:
        if not isinstance(success, bool):
            raise TypeError("success must be a boolean")
        duration_value = _validate_number(
            duration,
            "duration",
            minimum=0.0,
        )
        if execution_time is not None:
            execution_time = _validate_number(
                execution_time,
                "execution_time",
                minimum=0.0,
            )
        with self._lock:
            self._metrics[metric_name].record(
                success,
                duration_value,
                execution_time,
            )


def _validate_count(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(
            f"{field_name} must be a finite number greater than or equal to "
            f"{minimum}"
        )
    return float(value)
