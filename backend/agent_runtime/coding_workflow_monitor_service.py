"""Explicit bounded monitor resume gate for flashed coding workflows."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from ..runtime.result import ObserveResult
from ..tools.board_detector import BoardInfo, BoardType
from ..tools.serial_monitor import SerialMonitor, SerialMonitorConfig
from ..workflow.adapters.monitor_adapter import MonitorAdapter, MonitorAdapterError
from .coding_workflow_service import UNIFIED_CODING_WORKFLOW_FLAG, WORKFLOW_DISABLED
from .coding_workflow_store import (
    RUN_NOT_FOUND,
    RUN_STATUS_INVALID,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)


MONITOR_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_MONITOR_CONFIRMATION_REQUIRED"
NOT_AWAITING_MONITOR = "CODING_WORKFLOW_NOT_AWAITING_MONITOR"
MONITOR_ALREADY_COMPLETED = "CODING_WORKFLOW_MONITOR_ALREADY_COMPLETED"
MONITOR_PORT_REQUIRED = "CODING_WORKFLOW_MONITOR_PORT_REQUIRED"
MONITOR_PRECHECK_FAILED = "CODING_WORKFLOW_MONITOR_PRECHECK_FAILED"
MONITOR_FAILED = "CODING_WORKFLOW_MONITOR_FAILED"
MONITOR_PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"

DEFAULT_BAUD_RATE = 115_200
DEFAULT_DURATION_SECONDS = 5.0
MAX_DURATION_SECONDS = 10.0
DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024
MAX_OUTPUT_BYTES = 16 * 1024
MAX_PERSISTED_PREVIEW_BYTES = 1024

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Za-z0-9_-]*(?:api[_-]?key|token|password|cookie|credential|secret)"
    r"[A-Za-z0-9_-]*\b\s*[:=]\s*[^\s,;]*"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_WINDOWS_PATH = re.compile(r"(?i)(?:[a-z]:\\|\\\\)[^\s\"']+")
_POSIX_PATH = re.compile(r"(?<![\w.])/(?:[^\s/]+/)+[^\s\"']*")


class CodingWorkflowMonitorExecutor(Protocol):
    async def observe(self) -> ObserveResult: ...


@dataclass(frozen=True, slots=True)
class CodingWorkflowMonitorResult:
    run_id: str
    status: str
    review_id: str | None = None
    next_action: str | None = None
    monitor_status: str | None = None
    port: str | None = None
    baud_rate: int | None = None
    duration_ms: int | None = None
    output_byte_count: int = 0
    output_preview: str = ""
    truncated: bool = False
    failure_code: str | None = None
    safe_message: str = ""

    @property
    def completed(self) -> bool:
        return self.status == "completed" and self.monitor_status == "completed" and self.failure_code is None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "monitor_status": self.monitor_status,
            "port": self.port,
            "baud_rate": self.baud_rate,
            "duration_ms": self.duration_ms,
            "output_byte_count": self.output_byte_count,
            "output_preview": self.output_preview,
            "truncated": self.truncated,
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
        }


class CodingWorkflowMonitorService:
    """Runs one bounded ForgeX serial observation and completes the workflow."""

    def __init__(
        self,
        *,
        store: CodingWorkflowStore,
        monitor_factory: Callable[[SerialMonitorConfig], CodingWorkflowMonitorExecutor] = SerialMonitor,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._monitor_factory = monitor_factory
        source = os.environ if env is None else env
        self._enabled = source.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"

    async def run_monitor_for_flashed_workflow(
        self,
        run_id: str,
        *,
        monitor_confirmed: bool = False,
        port: str | None = None,
        baud_rate: int | None = None,
        duration_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> CodingWorkflowMonitorResult:
        if not self._enabled:
            return self._rejected(run_id, WORKFLOW_DISABLED, "Unified coding workflow is disabled.")
        if monitor_confirmed is not True:
            return self._rejected(run_id, MONITOR_CONFIRMATION_REQUIRED, "Explicit monitor confirmation is required.")
        try:
            run = self._store.get_run(run_id)
        except CodingWorkflowStoreError as exc:
            code = RUN_NOT_FOUND if exc.code == RUN_NOT_FOUND else MONITOR_PERSISTENCE_FAILED
            message = "Coding workflow run was not found." if code == RUN_NOT_FOUND else "Coding workflow state could not be read safely."
            return self._rejected(run_id, code, message)
        if run.status == "completed":
            return self._from_run(run, failure_code=MONITOR_ALREADY_COMPLETED, message="Coding workflow monitor already completed.")
        if run.status != "awaiting_monitor":
            return self._from_run(run, failure_code=NOT_AWAITING_MONITOR, message="Coding workflow run is not awaiting monitor.")

        recovered_port = run.metadata.get("flash_port")
        selected_port = port if port is not None else recovered_port
        if selected_port is None:
            return self._persist_failure(run, MONITOR_PORT_REQUIRED, "An explicit or previously validated monitor port is required.", expected="awaiting_monitor")
        try:
            safe_port = self._bounded_port(selected_port)
            if isinstance(recovered_port, str) and recovered_port.casefold() != safe_port.casefold():
                raise ValueError("monitor port does not match flashed port")
            baud = DEFAULT_BAUD_RATE if baud_rate is None else baud_rate
            duration = DEFAULT_DURATION_SECONDS if duration_seconds is None else duration_seconds
            output_limit = DEFAULT_MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes
            self._validate_inputs(baud, duration, output_limit)
            board = self._validated_linkage(run, safe_port)
            line_limit = max(64, min(1024, output_limit))
            log_limit = max(1, min(256, (output_limit // line_limit) + 1))
            config = MonitorAdapter.to_monitor_config(
                board,
                baudrate=baud,
                timeout_s=float(duration),
                connect_timeout_s=min(float(duration), 10.0),
                read_timeout=0.1,
                max_line_length=line_limit,
                max_log_lines=log_limit,
                drain_on_connect=True,
            )
        except (MonitorAdapterError, OSError, TypeError, ValueError):
            return self._persist_failure(run, MONITOR_PRECHECK_FAILED, "Flashed workflow failed the monitor precheck.", expected="awaiting_monitor")

        metadata = dict(run.metadata)
        metadata.update({"monitor_status": "running", "monitor_port": safe_port, "monitor_baud_rate": baud})
        try:
            monitoring = self._store.transition_run(
                run.run_id,
                expected_statuses=("awaiting_monitor",),
                status="monitoring",
                generation_status="review_created",
                next_action=None,
                safe_message="Confirmed bounded ForgeX monitor is in progress.",
                metadata=metadata,
            )
            self._append_event(monitoring.run_id, "monitor.started", "monitor", "monitoring", "Confirmed bounded ForgeX monitor started.", metadata={"port": safe_port, "baud_rate": baud, "duration_seconds": float(duration)})
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=MONITOR_PERSISTENCE_FAILED, message="Monitor start state could not be persisted safely.")

        try:
            observed = await self._monitor_factory(config).observe()
        except Exception:
            return self._persist_failure(run, MONITOR_FAILED, "ForgeX monitor did not complete successfully.", expected="monitoring")
        if not isinstance(observed, ObserveResult) or not observed.success:
            return self._persist_failure(run, MONITOR_FAILED, "ForgeX monitor did not complete successfully.", expected="monitoring")
        if observed.port and observed.port.casefold() != safe_port.casefold():
            return self._persist_failure(run, MONITOR_FAILED, "ForgeX monitor returned an unexpected serial port.", expected="monitoring")

        preview, output_count, truncated = self._bounded_output(observed, output_limit)
        persisted_preview, _, preview_truncated = self._truncate_utf8(preview, MAX_PERSISTED_PREVIEW_BYTES)
        truncated = truncated or preview_truncated
        duration_ms = min(2_147_483_647, max(0, observed.monitoring_duration_ms or observed.duration_ms))
        metadata = dict(monitoring.metadata)
        metadata.update({
            "monitor_status": "completed",
            "monitor_port": safe_port,
            "monitor_baud_rate": baud,
            "monitor_duration_ms": duration_ms,
            "monitor_output_byte_count": output_count,
            "monitor_output_truncated": truncated,
            "monitor_output_preview": persisted_preview,
            "monitor_lines_captured": min(2_147_483_647, max(0, observed.lines_captured)),
        })
        try:
            completed = self._store.transition_run(
                run.run_id,
                expected_statuses=("monitoring",),
                status="completed",
                generation_status="review_created",
                next_action=None,
                safe_message="Bounded monitor capture completed the unified coding workflow.",
                metadata=metadata,
            )
            output_event = persisted_preview or "No device output captured."
            self._append_event(completed.run_id, "monitor.output", "monitor", "completed", output_event, metadata={"output_byte_count": output_count, "truncated": truncated})
            self._append_event(completed.run_id, "monitor.completed", "monitor", "completed", "Bounded ForgeX monitor completed successfully.")
            self._append_event(completed.run_id, "workflow.completed", "workflow", "completed", "Unified coding workflow completed successfully.")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=MONITOR_PERSISTENCE_FAILED, message="Monitor completed, but workflow completion state could not be persisted safely.", monitor_status="completed")
        return self._from_run(
            completed,
            message="Unified coding workflow completed successfully.",
            monitor_status="completed",
            port=safe_port,
            baud_rate=baud,
            duration_ms=duration_ms,
            output_byte_count=output_count,
            output_preview=preview,
            truncated=truncated,
        )

    @staticmethod
    def _validated_linkage(run: CodingWorkflowRunRecord, port: str) -> BoardInfo:
        if (
            not run.review_id
            or not isinstance(run.metadata.get("apply_id"), str)
            or run.metadata.get("build_status") != "succeeded"
            or run.metadata.get("flash_status") != "succeeded"
        ):
            raise ValueError("workflow linkage is invalid")
        board_value = run.metadata.get("flash_board")
        if not isinstance(board_value, str):
            raise ValueError("flash board metadata missing")
        try:
            board_type = BoardType(board_value)
        except ValueError as exc:
            raise ValueError("flash board metadata invalid") from exc
        if board_type is BoardType.UNKNOWN:
            raise ValueError("flash board metadata invalid")
        return BoardInfo(board_type, port, None, None, None, None, None)

    @staticmethod
    def _validate_inputs(baud: object, duration: object, output_limit: object) -> None:
        if not isinstance(baud, int) or isinstance(baud, bool) or baud <= 0 or baud > 4_000_000:
            raise ValueError("baud rate is invalid")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 < float(duration) <= MAX_DURATION_SECONDS:
            raise ValueError("monitor duration is invalid")
        if not isinstance(output_limit, int) or isinstance(output_limit, bool) or not 1 <= output_limit <= MAX_OUTPUT_BYTES:
            raise ValueError("monitor output limit is invalid")

    @classmethod
    def _bounded_output(cls, observed: ObserveResult, limit: int) -> tuple[str, int, bool]:
        raw_observations = observed.metadata.get("observations", [])
        lines: list[str] = []
        if isinstance(raw_observations, list):
            for item in raw_observations:
                if isinstance(item, Mapping) and item.get("source") == "DEVICE" and isinstance(item.get("line"), str):
                    lines.append(cls._sanitize_output(str(item["line"])))
        text = "\n".join(lines)
        preview, byte_count, truncated = cls._truncate_utf8(text, limit)
        dropped = observed.metadata.get("observations_dropped", 0)
        if isinstance(dropped, int) and not isinstance(dropped, bool) and dropped > 0:
            truncated = True
        return preview, byte_count, truncated

    @staticmethod
    def _sanitize_output(value: str) -> str:
        value = "".join(character if character in "\n\t" or character.isprintable() else "�" for character in value)
        value = _SECRET_ASSIGNMENT.sub("[REDACTED]", value)
        value = _BEARER.sub("Bearer [REDACTED]", value)
        value = _WINDOWS_PATH.sub("[PATH]", value)
        value = _POSIX_PATH.sub("[PATH]", value)
        return value.replace("\r", "").strip()

    @staticmethod
    def _truncate_utf8(value: str, limit: int) -> tuple[str, int, bool]:
        encoded = value.encode("utf-8")
        if len(encoded) <= limit:
            return value, len(encoded), False
        clipped = encoded[:limit]
        while clipped:
            try:
                preview = clipped.decode("utf-8")
                break
            except UnicodeDecodeError:
                clipped = clipped[:-1]
        else:
            preview = ""
        return preview, len(encoded), True

    def _persist_failure(self, run: CodingWorkflowRunRecord, code: str, message: str, *, expected: str) -> CodingWorkflowMonitorResult:
        metadata = dict(run.metadata)
        metadata.update({"monitor_status": "failed"})
        try:
            failed = self._store.transition_run(
                run.run_id, expected_statuses=(expected,), status="failed", generation_status="failed",
                next_action=None, failure_code=code, safe_message=message, metadata=metadata,
            )
            self._append_event(failed.run_id, "monitor.failed", "monitor", "failed", message, metadata={"failure_code": code})
            self._append_event(failed.run_id, "workflow.failed", "workflow", "failed", "Unified coding workflow failed during bounded monitor capture.", metadata={"failure_code": code})
            return self._from_run(failed, failure_code=code, message=message, monitor_status="failed")
        except CodingWorkflowStoreError as exc:
            fallback = NOT_AWAITING_MONITOR if exc.code == RUN_STATUS_INVALID else MONITOR_PERSISTENCE_FAILED
            return self._from_run(run, failure_code=fallback, message="Monitor failure state could not be persisted safely.", monitor_status="failed")

    def _append_event(self, run_id: str, event_type: str, stage: str, status: str, message: str, *, metadata: Mapping[str, object] | None = None) -> None:
        sequence = len(self._store.list_events(run_id)) + 1
        digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
        self._store.append_event(CodingWorkflowEventRecord(
            event_id=f"event-{digest}", run_id=run_id, sequence=sequence, event_type=event_type,
            stage=stage, status=status, safe_message=message, metadata=dict(metadata or {}),
        ))

    @staticmethod
    def _bounded_port(value: object) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value.strip()) > 256:
            raise ValueError("monitor port is invalid")
        return value.strip()

    @staticmethod
    def _rejected(run_id: str, code: str, message: str) -> CodingWorkflowMonitorResult:
        return CodingWorkflowMonitorResult(run_id=run_id, status="rejected", failure_code=code, safe_message=message)

    @staticmethod
    def _from_run(
        run: CodingWorkflowRunRecord, *, failure_code: str | None = None, message: str,
        monitor_status: str | None = None, port: str | None = None, baud_rate: int | None = None,
        duration_ms: int | None = None, output_byte_count: int = 0, output_preview: str = "",
        truncated: bool = False,
    ) -> CodingWorkflowMonitorResult:
        return CodingWorkflowMonitorResult(
            run_id=run.run_id, status=run.status, review_id=run.review_id, next_action=run.next_action,
            monitor_status=monitor_status, port=port, baud_rate=baud_rate, duration_ms=duration_ms,
            output_byte_count=output_byte_count, output_preview=output_preview, truncated=truncated,
            failure_code=failure_code, safe_message=message,
        )
