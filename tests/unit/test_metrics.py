from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.observability.metrics import (
    MetricCounter,
    MetricTimer,
    MetricsRegistry,
)


def test_counter_tracks_and_resets_values() -> None:
    counter = MetricCounter(2)

    assert counter.increment() == 3
    assert counter.increment(4) == 7
    assert counter.value == 7

    counter.reset()
    assert counter.value == 0


def test_timer_tracks_average_and_latest_time() -> None:
    timer = MetricTimer(clock=lambda: 123.0)

    timer.record(10)
    timer.record(20, execution_time=456.0)

    assert timer.count == 2
    assert timer.total_duration == 30.0
    assert timer.average_duration == 15.0
    assert timer.last_execution_time == 456.0

    timer.reset()
    assert timer.snapshot() == (0, 0.0, None)


def test_registry_records_all_operation_types() -> None:
    registry = MetricsRegistry(clock=lambda: 1000.0)

    registry.record_execution(True, 10)
    registry.record_execution(False, 30, execution_time=1001.0)
    registry.record_generation(True, 20)
    registry.record_build(False, 40)
    registry.record_flash(True, 50)
    registry.record_monitor_session(True, 60)

    metrics = registry.get_metrics()
    assert tuple(metrics) == (
        "executions",
        "generations",
        "builds",
        "flashes",
        "monitor_sessions",
    )
    assert metrics["executions"] == {
        "success_count": 1,
        "failure_count": 1,
        "average_duration": 20.0,
        "last_execution_time": 1001.0,
    }
    assert metrics["generations"]["success_count"] == 1
    assert metrics["builds"]["failure_count"] == 1
    assert metrics["flashes"]["average_duration"] == 50.0
    assert metrics["monitor_sessions"]["average_duration"] == 60.0


def test_registry_snapshot_is_detached_and_reset_is_complete() -> None:
    registry = MetricsRegistry(clock=lambda: 1000.0)
    registry.record_execution(True, 10)
    first = registry.get_metrics()
    first["executions"]["success_count"] = 999

    assert registry.get_metrics()["executions"]["success_count"] == 1

    registry.reset_metrics()
    assert all(
        metric == {
            "success_count": 0,
            "failure_count": 0,
            "average_duration": 0.0,
            "last_execution_time": None,
        }
        for metric in registry.get_metrics().values()
    )


def test_registry_is_thread_safe() -> None:
    registry = MetricsRegistry(clock=lambda: 1000.0)

    def record(index: int) -> None:
        registry.record_build(index % 2 == 0, float(index))

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(record, range(100)))

    metric = registry.get_metrics()["builds"]
    assert metric["success_count"] == 50
    assert metric["failure_count"] == 50
    assert metric["average_duration"] == 49.5


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True])
def test_invalid_durations_are_rejected(value: object) -> None:
    registry = MetricsRegistry()

    with pytest.raises(ValueError):
        registry.record_execution(True, value)  # type: ignore[arg-type]


def test_non_boolean_success_is_rejected() -> None:
    registry = MetricsRegistry()

    with pytest.raises(TypeError):
        registry.record_execution(1, 10)  # type: ignore[arg-type]


def test_timer_rejects_aggregate_overflow_without_changing_state() -> None:
    timer = MetricTimer(clock=lambda: 1.0)
    timer.record(1e308)

    with pytest.raises(ValueError, match="total duration"):
        timer.record(1e308)

    assert timer.count == 1
    assert timer.total_duration == 1e308
