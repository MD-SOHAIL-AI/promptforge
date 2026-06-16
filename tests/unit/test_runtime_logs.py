from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.observability.runtime_logs import RuntimeLog, RuntimeLogger


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class FailingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("sink unavailable")


def test_runtime_log_is_immutable_and_json_compatible() -> None:
    item = RuntimeLog(
        timestamp=NOW,
        log_type="workflow",
        level="INFO",
        message="workflow started",
        correlation_id="correlation-1",
        task_id="task-1",
        execution_id="execution-1",
        metadata={"steps": ["plan", "build"]},
    )

    assert item.to_dict() == {
        "timestamp": "2026-01-02T03:04:05Z",
        "log_type": "workflow",
        "level": "INFO",
        "message": "workflow started",
        "correlation_id": "correlation-1",
        "task_id": "task-1",
        "execution_id": "execution-1",
        "metadata": {"steps": ["plan", "build"]},
    }
    assert json.loads(item.to_json()) == item.to_dict()


def test_logger_records_and_emits_every_log_type() -> None:
    stdlib_logger = logging.Logger("test.runtime")
    handler = ListHandler()
    stdlib_logger.addHandler(handler)
    runtime = RuntimeLogger(stdlib_logger, clock=lambda: NOW)
    kwargs = {
        "correlation_id": "correlation-1",
        "task_id": "task-1",
        "execution_id": "execution-1",
    }

    runtime.log_workflow("workflow", **kwargs)
    runtime.log_generation("generation", **kwargs)
    runtime.log_build("build", level=logging.WARNING, **kwargs)
    runtime.log_flash("flash", **kwargs)
    runtime.log_monitor("monitor", **kwargs)

    exported = json.loads(runtime.export_logs())
    assert [item["log_type"] for item in exported] == [
        "workflow",
        "generation",
        "build",
        "flash",
        "monitor",
    ]
    assert len(handler.messages) == 5
    assert json.loads(handler.messages[2])["level"] == "WARNING"


def test_generated_correlation_ids_and_bounded_retention() -> None:
    identifiers = iter(("correlation-1", "correlation-2", "correlation-3"))
    runtime = RuntimeLogger(
        logging.Logger("bounded"),
        max_logs=2,
        clock=lambda: NOW,
        correlation_id_factory=lambda: next(identifiers),
    )

    runtime.log_workflow("one")
    runtime.log_workflow("two")
    runtime.log_workflow("three")

    exported = json.loads(runtime.export_logs())
    assert [item["correlation_id"] for item in exported] == [
        "correlation-2",
        "correlation-3",
    ]


def test_jsonl_and_atomic_file_export(tmp_path: Path) -> None:
    runtime = RuntimeLogger(logging.Logger("export"), clock=lambda: NOW)
    runtime.log_build("first", correlation_id="correlation-1")
    runtime.log_build("second", correlation_id="correlation-1")
    destination = tmp_path / "logs" / "runtime.jsonl"

    content = runtime.export_logs(destination, format="jsonl", clear=True)

    assert destination.read_text(encoding="utf-8") == content + "\n"
    assert [json.loads(line)["message"] for line in content.splitlines()] == [
        "first",
        "second",
    ]
    assert runtime.log_count == 0


def test_concurrent_logging_preserves_every_retained_event() -> None:
    runtime = RuntimeLogger(
        logging.Logger("concurrent"),
        max_logs=200,
        clock=lambda: NOW,
    )

    def log(index: int) -> None:
        runtime.log_monitor(
            f"line-{index}",
            correlation_id="correlation-1",
            task_id="task-1",
            execution_id="execution-1",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        tuple(executor.map(log, range(100)))

    exported = json.loads(runtime.export_logs())
    assert len(exported) == 100
    assert {item["message"] for item in exported} == {
        f"line-{index}" for index in range(100)
    }


@pytest.mark.parametrize(
    "metadata",
    [
        {"number": float("nan")},
        {"invalid": object()},
        {"invalid": b"bytes"},
    ],
)
def test_invalid_metadata_is_rejected(metadata: object) -> None:
    runtime = RuntimeLogger(logging.Logger("invalid"), clock=lambda: NOW)

    with pytest.raises(ValueError):
        runtime.log_workflow(
            "message",
            correlation_id="correlation-1",
            metadata=metadata,  # type: ignore[arg-type]
        )


def test_logging_sink_failure_does_not_drop_or_raise() -> None:
    stdlib_logger = logging.Logger("failing")
    stdlib_logger.addHandler(FailingHandler())
    runtime = RuntimeLogger(stdlib_logger, clock=lambda: NOW)

    item = runtime.log_workflow(
        "message",
        "correlation-1",
        "task-1",
        "execution-1",
    )

    assert item.message == "message"
    assert runtime.log_count == 1
