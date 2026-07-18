"""Explicit flash resume gate for successfully built coding workflows."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..bridges.diff_service import hash_file
from ..bridges.patch_apply_models import PatchApplyResult
from ..runtime.result import FlashResult
from ..tools.board_detector import BoardInfo, BoardType
from ..tools.build_firmware import BuildArtifact
from ..tools.flash_firmware import FlashConfig
from ..validation.board_validator import BoardValidator
from ..workflow.adapters.flash_adapter import FlashAdapter, FlashAdapterError
from .coding_workflow_service import UNIFIED_CODING_WORKFLOW_FLAG, WORKFLOW_DISABLED
from .coding_workflow_store import (
    RUN_NOT_FOUND,
    RUN_STATUS_INVALID,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)


FLASH_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_FLASH_CONFIRMATION_REQUIRED"
NOT_AWAITING_FLASH = "CODING_WORKFLOW_NOT_AWAITING_FLASH"
FLASH_ALREADY_COMPLETED = "CODING_WORKFLOW_FLASH_ALREADY_COMPLETED"
BUILD_ARTIFACT_NOT_FOUND = "CODING_WORKFLOW_BUILD_ARTIFACT_NOT_FOUND"
WORKSPACE_NOT_FOUND = "CODING_WORKFLOW_WORKSPACE_NOT_FOUND"
FLASH_PRECHECK_FAILED = "CODING_WORKFLOW_FLASH_PRECHECK_FAILED"
FLASH_DEVICE_REQUIRED = "CODING_WORKFLOW_FLASH_DEVICE_REQUIRED"
FLASH_FAILED = "CODING_WORKFLOW_FLASH_FAILED"
FLASH_PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"


class CodingWorkflowFlashExecutor(Protocol):
    async def flash(self, artifact: BuildArtifact, board: BoardInfo, config: FlashConfig) -> FlashResult: ...


class CodingWorkflowBoardDetector(Protocol):
    def detect_boards(self) -> list[BoardInfo]: ...


class CodingWorkflowApplyLookup(Protocol):
    def get_apply(self, apply_id: str) -> PatchApplyResult: ...


@dataclass(frozen=True, slots=True)
class CodingWorkflowFlashResult:
    run_id: str
    status: str
    review_id: str | None = None
    next_action: str | None = None
    flash_status: str | None = None
    artifact_reference: str | None = None
    environment: str | None = None
    board: str | None = None
    port: str | None = None
    duration_ms: int | None = None
    failure_code: str | None = None
    safe_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "awaiting_monitor" and self.flash_status == "succeeded" and self.failure_code is None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "flash_status": self.flash_status,
            "artifact_reference": self.artifact_reference,
            "environment": self.environment,
            "board": self.board,
            "port": self.port,
            "duration_ms": self.duration_ms,
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
        }


class CodingWorkflowFlashService:
    """Runs ForgeX's canonical flasher once and stops before monitor."""

    def __init__(
        self,
        *,
        store: CodingWorkflowStore,
        flash_service: CodingWorkflowFlashExecutor,
        board_detector: CodingWorkflowBoardDetector,
        patch_apply_service: CodingWorkflowApplyLookup,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._flash = flash_service
        self._boards = board_detector
        self._applies = patch_apply_service
        source = os.environ if env is None else env
        self._enabled = source.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"

    async def run_flash_for_built_workflow(
        self,
        run_id: str,
        *,
        flash_confirmed: bool = False,
        workspace_path: str | Path | None = None,
        port: str | None = None,
        board_id: str | BoardType | None = None,
    ) -> CodingWorkflowFlashResult:
        if not self._enabled:
            return self._rejected(run_id, WORKFLOW_DISABLED, "Unified coding workflow is disabled.")
        if flash_confirmed is not True:
            return self._rejected(run_id, FLASH_CONFIRMATION_REQUIRED, "Explicit flash confirmation is required.")
        try:
            run = self._store.get_run(run_id)
        except CodingWorkflowStoreError as exc:
            code = RUN_NOT_FOUND if exc.code == RUN_NOT_FOUND else FLASH_PERSISTENCE_FAILED
            message = "Coding workflow run was not found." if code == RUN_NOT_FOUND else "Coding workflow state could not be read safely."
            return self._rejected(run_id, code, message)

        if run.status == "awaiting_monitor":
            return self._from_run(run, failure_code=FLASH_ALREADY_COMPLETED, message="Coding workflow flash already completed.")
        if run.status != "awaiting_flash":
            return self._from_run(run, failure_code=NOT_AWAITING_FLASH, message="Coding workflow run is not awaiting flash.")
        if workspace_path is None:
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Built workflow workspace is unavailable for flash.", expected="awaiting_flash")
        try:
            root = Path(workspace_path).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Built workflow workspace is unavailable for flash.", expected="awaiting_flash")
        if not root.is_dir() or not (root / "platformio.ini").is_file():
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Built workflow workspace is unavailable for flash.", expected="awaiting_flash")
        if port is None or board_id is None:
            return self._persist_failure(run, FLASH_DEVICE_REQUIRED, "An explicit flash port and board identifier are required.", expected="awaiting_flash")
        try:
            safe_port = self._bounded_display(port, "flash port", max_chars=256)
            requested_type = self._board_type(board_id)
            self._validate_built_board(run, requested_type)
            artifact = self._validated_artifact(run, root)
            board = self._detected_board(safe_port, requested_type)
            compatibility = BoardValidator().validate(
                {
                    "target_board": requested_type.value,
                    "framework": "Arduino",
                    "firmware_size_bytes": artifact.size_bytes,
                },
                {"target_board": board.board_type.value},
            )
            if not compatibility.compatible:
                raise ValueError("board validation failed")
            config = FlashAdapter.to_flash_config(board, artifact)
        except _ArtifactMissing:
            return self._persist_failure(run, BUILD_ARTIFACT_NOT_FOUND, "Built firmware artifact is unavailable for flash.", expected="awaiting_flash")
        except _DeviceMissing:
            return self._persist_failure(run, FLASH_DEVICE_REQUIRED, "The explicitly requested flash device is unavailable.", expected="awaiting_flash")
        except (FlashAdapterError, OSError, TypeError, ValueError):
            return self._persist_failure(run, FLASH_PRECHECK_FAILED, "Built workflow failed the flash integrity precheck.", expected="awaiting_flash")
        except Exception:
            return self._persist_failure(run, FLASH_PRECHECK_FAILED, "Flash device validation could not complete safely.", expected="awaiting_flash")

        metadata = dict(run.metadata)
        metadata.update({"flash_status": "running", "flash_port": safe_port})
        try:
            flashing = self._store.transition_run(
                run.run_id,
                expected_statuses=("awaiting_flash",),
                status="flashing",
                generation_status="review_created",
                next_action=None,
                safe_message="Confirmed ForgeX flash is in progress.",
                metadata=metadata,
            )
            self._append_event(flashing.run_id, "flash.started", "flash", "flashing", "Confirmed ForgeX flash started.", metadata={"port": safe_port, "board": requested_type.value})
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=FLASH_PERSISTENCE_FAILED, message="Flash start state could not be persisted safely.")

        try:
            flashed = await self._flash.flash(artifact, board, config)
        except Exception:
            return self._persist_failure(run, FLASH_FAILED, "ForgeX flash did not complete successfully.", expected="flashing")
        if (
            not isinstance(flashed, FlashResult)
            or not flashed.success
            or flashed.port.strip().casefold() != safe_port.casefold()
            or flashed.board.strip().casefold() != requested_type.value.casefold()
        ):
            return self._persist_failure(run, FLASH_FAILED, "ForgeX flash did not complete successfully.", expected="flashing")

        duration_ms = min(2_147_483_647, max(0, flashed.flash_duration_ms or flashed.duration_ms))
        metadata = dict(flashing.metadata)
        metadata.update({
            "flash_status": "succeeded",
            "flash_port": safe_port,
            "flash_board": requested_type.value,
            "flash_environment": artifact.environment,
            "flash_artifact_reference": run.metadata["artifact_reference"],
            "flash_duration_ms": duration_ms,
            "flash_bytes_written": min(2_147_483_647, max(0, flashed.bytes_written)),
        })
        try:
            completed = self._store.transition_run(
                run.run_id,
                expected_statuses=("flashing",),
                status="awaiting_monitor",
                generation_status="review_created",
                next_action="open_monitor",
                safe_message="Flash succeeded and is waiting for an explicit monitor action.",
                metadata=metadata,
            )
            event_metadata = {"port": safe_port, "board": requested_type.value, "duration_ms": duration_ms}
            self._append_event(completed.run_id, "flash.completed", "flash", "awaiting_monitor", "ForgeX flash completed successfully.", metadata=event_metadata)
            self._append_event(completed.run_id, "monitor.waiting_to_start", "monitor", "awaiting_monitor", "Monitor is waiting for an explicit start action.")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=FLASH_PERSISTENCE_FAILED, message="Flash succeeded, but workflow completion state could not be persisted safely.", flash_status="succeeded")
        return self._from_run(
            completed,
            message="ForgeX flash completed successfully.",
            flash_status="succeeded",
            artifact_reference=str(run.metadata["artifact_reference"]),
            environment=artifact.environment,
            board=requested_type.value,
            port=safe_port,
            duration_ms=duration_ms,
        )

    def _validated_artifact(self, run: CodingWorkflowRunRecord, root: Path) -> BuildArtifact:
        if not run.review_id or run.metadata.get("build_status") != "succeeded":
            raise ValueError("build metadata missing")
        apply_id = run.metadata.get("apply_id")
        if not isinstance(apply_id, str) or not apply_id:
            raise ValueError("apply metadata missing")
        applied = self._applies.get_apply(apply_id)
        if applied.status != "applied" or applied.review_id != run.review_id or applied.provider_id != run.provider_id:
            raise ValueError("apply metadata mismatch")
        reference = run.metadata.get("artifact_reference")
        artifact_type = run.metadata.get("artifact_type")
        size = run.metadata.get("artifact_size_bytes")
        artifact_sha256 = run.metadata.get("artifact_sha256")
        environment = run.metadata.get("build_environment")
        if not isinstance(reference, str) or not reference or len(reference) > 512 or "\x00" in reference:
            raise _ArtifactMissing
        target = (root / reference).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise _ArtifactMissing from exc
        if not target.is_file() or target.is_symlink():
            raise _ArtifactMissing
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0 or target.stat().st_size != size:
            raise _ArtifactMissing
        if not isinstance(artifact_sha256, str) or hash_file(target) != artifact_sha256:
            raise _ArtifactMissing
        if not isinstance(artifact_type, str) or not isinstance(environment, str):
            raise ValueError("artifact metadata invalid")
        return BuildArtifact(path=target, environment=environment, artifact_type=artifact_type, size_bytes=size)

    def _detected_board(self, port: str, expected_type: BoardType) -> BoardInfo:
        boards = self._boards.detect_boards()
        board = next((item for item in boards if item.port.strip().casefold() == port.casefold()), None)
        if board is None:
            raise _DeviceMissing
        if board.board_type is not expected_type:
            raise ValueError("detected board type mismatch")
        return board

    @classmethod
    def _validate_built_board(cls, run: CodingWorkflowRunRecord, expected_type: BoardType) -> None:
        built_board = run.metadata.get("build_board")
        if built_board is not None and (
            not isinstance(built_board, str) or cls._board_type(built_board) is not expected_type
        ):
            raise ValueError("built board metadata mismatch")

    @staticmethod
    def _board_type(value: str | BoardType) -> BoardType:
        if isinstance(value, BoardType):
            if value is BoardType.UNKNOWN:
                raise ValueError("unknown board")
            return value
        text = value.strip().casefold() if isinstance(value, str) else ""
        direct = next((item for item in BoardType if item.value.casefold() == text), None)
        if direct is not None and direct is not BoardType.UNKNOWN:
            return direct
        normalized = text.replace("-", "_").replace(" ", "_")
        if normalized.startswith("esp32") and "s3" in normalized:
            return BoardType.ESP32_S3
        if normalized.startswith("esp32") and "c3" in normalized:
            return BoardType.ESP32_C3
        if normalized.startswith("esp32"):
            return BoardType.ESP32
        if normalized.startswith("stm32") or normalized.startswith("nucleo"):
            return BoardType.STM32
        if normalized in {"uno", "uno_r3", "arduino_uno"}:
            return BoardType.ARDUINO_UNO
        raise ValueError("unsupported board identifier")

    def _persist_failure(self, run: CodingWorkflowRunRecord, code: str, message: str, *, expected: str) -> CodingWorkflowFlashResult:
        metadata = dict(run.metadata)
        metadata.update({"flash_status": "failed"})
        try:
            failed = self._store.transition_run(
                run.run_id,
                expected_statuses=(expected,),
                status="failed",
                generation_status="failed",
                next_action=None,
                failure_code=code,
                safe_message=message,
                metadata=metadata,
            )
            self._append_event(failed.run_id, "flash.failed", "flash", "failed", message, metadata={"failure_code": code})
            return self._from_run(failed, failure_code=code, message=message, flash_status="failed")
        except CodingWorkflowStoreError as exc:
            fallback = NOT_AWAITING_FLASH if exc.code == RUN_STATUS_INVALID else FLASH_PERSISTENCE_FAILED
            return self._from_run(run, failure_code=fallback, message="Flash failure state could not be persisted safely.", flash_status="failed")

    def _append_event(self, run_id: str, event_type: str, stage: str, status: str, message: str, *, metadata: Mapping[str, object] | None = None) -> None:
        sequence = len(self._store.list_events(run_id)) + 1
        digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
        self._store.append_event(CodingWorkflowEventRecord(
            event_id=f"event-{digest}", run_id=run_id, sequence=sequence, event_type=event_type,
            stage=stage, status=status, safe_message=message, metadata=dict(metadata or {}),
        ))

    @staticmethod
    def _bounded_display(value: str, field_name: str, *, max_chars: int) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value.strip()) > max_chars:
            raise ValueError(f"{field_name} is invalid")
        return value.strip()

    @staticmethod
    def _rejected(run_id: str, code: str, message: str) -> CodingWorkflowFlashResult:
        return CodingWorkflowFlashResult(run_id=run_id, status="rejected", failure_code=code, safe_message=message)

    @staticmethod
    def _from_run(
        run: CodingWorkflowRunRecord, *, failure_code: str | None = None, message: str,
        flash_status: str | None = None, artifact_reference: str | None = None,
        environment: str | None = None, board: str | None = None, port: str | None = None,
        duration_ms: int | None = None,
    ) -> CodingWorkflowFlashResult:
        return CodingWorkflowFlashResult(
            run_id=run.run_id, status=run.status, review_id=run.review_id, next_action=run.next_action,
            flash_status=flash_status, artifact_reference=artifact_reference, environment=environment,
            board=board, port=port, duration_ms=duration_ms, failure_code=failure_code, safe_message=message,
        )


class _ArtifactMissing(ValueError):
    pass


class _DeviceMissing(ValueError):
    pass
