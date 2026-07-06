"""Generic AGY adapter over the authoritative legacy detector and runner."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ..models import BridgeDetectionResult as LegacyDetectionResult
from ..run_models import BridgeSandboxRun
from .antigravity_runner import (
    SAFE_ENV_NAMES,
    AntigravityRunnerError,
    AntigravitySandboxRunner,
)
from ..generic.contracts import BridgeProvider
from ..generic.errors import BridgeDomainError, BridgeErrorCode
from ..generic.models import (
    BridgeArtifactReference,
    BridgeArtifactType,
    BridgeCancellationDisposition,
    BridgeCancellationEscalation,
    BridgeCancellationResult,
    BridgeCapabilities,
    BridgeDetectionResult,
    BridgeEventType,
    BridgeRunContext,
    BridgeRunEvent,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeValidationResult,
)
from ..generic.states import BridgeRunStatus
from ..generic.validation import sanitize_message, utc_now, validate_safe_code


AGY_GENERIC_PROVIDER_ID = "agy"
SUPPORTED_AGY_CAPABILITIES = frozenset({"edit_files"})
LEGACY_TERMINAL_STATES = frozenset({"review_ready", "completed", "completed_no_changes", "failed", "cancelled", "failed_timeout", "blocked", "interrupted"})

LEGACY_AGY_STATE_MAP: dict[str, BridgeRunStatus] = {
    "detected": BridgeRunStatus.QUEUED,
    "accepted": BridgeRunStatus.QUEUED,
    "pending": BridgeRunStatus.PREPARING_SANDBOX,
    "validating": BridgeRunStatus.VALIDATING,
    "preparing_sandbox": BridgeRunStatus.PREPARING_SANDBOX,
    "running": BridgeRunStatus.RUNNING,
    "cancelling": BridgeRunStatus.CANCELLING,
    "cancellation_requested": BridgeRunStatus.CANCELLING,
    "collecting_changes": BridgeRunStatus.COLLECTING_ARTIFACTS,
    "review_ready": BridgeRunStatus.COMPLETED,
    "completed": BridgeRunStatus.COMPLETED,
    "completed_no_changes": BridgeRunStatus.FAILED,
    "failed": BridgeRunStatus.FAILED,
    "cancelled": BridgeRunStatus.CANCELLED,
    "failed_timeout": BridgeRunStatus.TIMED_OUT,
    "timed_out": BridgeRunStatus.TIMED_OUT,
    "blocked": BridgeRunStatus.BLOCKED,
    "interrupted": BridgeRunStatus.INTERRUPTED,
}

_GENERIC_TERMINAL = frozenset(
    {
        BridgeRunStatus.COMPLETED,
        BridgeRunStatus.FAILED,
        BridgeRunStatus.CANCELLED,
        BridgeRunStatus.TIMED_OUT,
        BridgeRunStatus.BLOCKED,
        BridgeRunStatus.INTERRUPTED,
    }
)


class AGYRunner(Protocol):
    def is_enabled(self) -> bool: ...

    def start_run(self, *, workspace_root: str | Path, prompt: str, timeout_seconds: int) -> BridgeSandboxRun: ...

    def start_run_in_sandbox(
        self, *, run_id: str, sandbox_root: str | Path, prompt: str, timeout_seconds: int, baseline_root: str | Path | None = None
    ) -> BridgeSandboxRun: ...

    def get_run(self, run_id: str) -> BridgeSandboxRun: ...

    def cancel_run(self, run_id: str) -> BridgeSandboxRun: ...


@dataclass(frozen=True, slots=True)
class AGYRunnerRequest:
    """Dedicated translation result; prompt and internal root are never represented publicly."""

    generic_run_id: str
    project_id: str
    sandbox_id: str
    timeout_seconds: int
    workspace_root: Path = field(repr=False)
    baseline_root: Path = field(repr=False)
    prompt: str = field(repr=False)

    def runner_kwargs(self) -> dict[str, object]:
        return {
            "workspace_root": self.workspace_root,
            "baseline_root": self.baseline_root,
            "prompt": self.prompt,
            "timeout_seconds": self.timeout_seconds,
        }

    def to_safe_metadata(self) -> dict[str, object]:
        return {
            "generic_run_id": self.generic_run_id,
            "project_id": self.project_id,
            "sandbox_id": self.sandbox_id,
            "timeout_seconds": self.timeout_seconds,
            "instruction_hash": hashlib.sha256(self.prompt.encode("utf-8")).hexdigest(),
            "instruction_length": len(self.prompt),
        }


def map_legacy_agy_state(status: str) -> BridgeRunStatus:
    """Unknown legacy states fail closed without extending the canonical model."""

    return LEGACY_AGY_STATE_MAP.get(status, BridgeRunStatus.FAILED)


class AGYBridgeProvider(BridgeProvider):
    """Translation-only adapter. It never constructs argv or starts a process itself."""

    provider_id = AGY_GENERIC_PROVIDER_ID
    environment_allowlist = frozenset(SAFE_ENV_NAMES)

    def __init__(
        self,
        *,
        detector: Callable[[], LegacyDetectionResult],
        runner: AGYRunner,
        artifact_root: str | Path,
        execution_enabled: bool = False,
        availability_override: bool = False,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self._detector = detector
        self._runner = runner
        self._artifact_root = Path(artifact_root).resolve()
        self._execution_enabled = execution_enabled
        self._poll_interval_seconds = max(0.001, poll_interval_seconds)
        self._detected_available = availability_override
        self._detected_version: str | None = None
        self._generic_to_legacy: dict[str, str] = {}
        self._events: dict[str, list[BridgeRunEvent]] = {}
        self._terminal_status: dict[str, BridgeRunStatus] = {}
        self._cancellation_requested: set[str] = set()
        self._cancellation_requested_at: dict[str, datetime] = {}
        self._cancellation_reason: dict[str, str] = {}
        self._last_legacy_status: dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def execution_enabled(self) -> bool:
        return self._execution_enabled

    def capabilities(self) -> BridgeCapabilities:
        return BridgeCapabilities(
            provider_id=self.provider_id,
            display_name="Google Antigravity / AGY CLI",
            provider_version=self._detected_version,
            available=self._detected_available,
            non_interactive=True,
            sandbox_required=True,
            supports_cancellation=True,
            supports_timeout=True,
            supports_artifacts=True,
            supports_structured_output=True,
            supports_streaming=False,
            supports_subscription_auth=False,
            supports_api_key_auth=False,
            supports_resume=False,
        )

    async def detect(self) -> BridgeDetectionResult:
        try:
            legacy = await asyncio.to_thread(self._detector)
        except Exception:
            self._detected_available = False
            self._detected_version = None
            return BridgeDetectionResult(
                provider_id=self.provider_id,
                installed=False,
                available=False,
                safe_message="AGY detection failed safely.",
                authentication_status="error",
                unavailability_code=BridgeErrorCode.PROVIDER_UNAVAILABLE.value,
                capabilities=self.capabilities(),
            )
        self._detected_available = bool(legacy.installed)
        self._detected_version = legacy.version
        unavailability_code = None if legacy.installed else BridgeErrorCode.PROVIDER_UNAVAILABLE.value
        safe_message = legacy.auth_message if legacy.installed else "AGY CLI is not installed."
        return BridgeDetectionResult(
            provider_id=self.provider_id,
            installed=legacy.installed,
            available=legacy.installed,
            safe_message=safe_message,
            provider_version=legacy.version,
            authentication_status=legacy.auth_status,
            unavailability_code=unavailability_code,
            capabilities=self.capabilities(),
        )

    async def validate(self, request: BridgeRunRequest) -> BridgeValidationResult:
        if request.provider_id != self.provider_id:
            return BridgeValidationResult(
                valid=False,
                failure_code=BridgeErrorCode.PROVIDER_UNSUPPORTED.value,
                safe_message="The bridge request targets a different provider.",
            )
        if request.requested_capability not in SUPPORTED_AGY_CAPABILITIES:
            return BridgeValidationResult(
                valid=False,
                failure_code=BridgeErrorCode.PROVIDER_UNSUPPORTED.value,
                safe_message="The requested AGY capability is unsupported.",
            )
        if not self._execution_enabled:
            return BridgeValidationResult(
                valid=False,
                failure_code=BridgeErrorCode.PROVIDER_DISABLED.value,
                safe_message="Generic AGY adapter execution is disabled.",
            )
        if not self._runner.is_enabled():
            return BridgeValidationResult(
                valid=False,
                failure_code=BridgeErrorCode.PROVIDER_DISABLED.value,
                safe_message="The existing AGY runner is disabled.",
            )
        return BridgeValidationResult(valid=True, safe_message="AGY request is valid for sandbox execution.")

    def translate_request(self, context: BridgeRunContext) -> AGYRunnerRequest:
        request = context.request
        if request.provider_id != self.provider_id:
            raise BridgeDomainError(BridgeErrorCode.PROVIDER_UNSUPPORTED)
        if request.requested_capability not in SUPPORTED_AGY_CAPABILITIES:
            raise BridgeDomainError(BridgeErrorCode.PROVIDER_UNSUPPORTED)
        sandbox = context.sandbox
        root = sandbox.internal_sandbox_root.resolve()
        if not sandbox.containment_verified:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_REQUIRED)
        try:
            root.relative_to(sandbox.managed_sandbox_root.resolve())
        except ValueError as exc:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED) from exc
        if root == sandbox.active_workspace_root.resolve():
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED)
        if not root.exists() or not root.is_dir():
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_INVALID)
        return AGYRunnerRequest(
            generic_run_id=request.run_id,
            project_id=request.project_id,
            sandbox_id=request.sandbox_id,
            timeout_seconds=request.timeout_seconds,
            workspace_root=root,
            baseline_root=sandbox.active_workspace_root.resolve(),
            prompt=request.instruction,
        )

    async def start(self, context: BridgeRunContext) -> BridgeRunResult:
        translated = self.translate_request(context)
        validation = await self.validate(context.request)
        if not validation.valid:
            self._append_terminal_event(
                context.request.run_id,
                BridgeRunStatus.BLOCKED,
                BridgeEventType.FAILURE,
                validation.safe_message,
                validation.failure_code,
            )
            return BridgeRunResult(
                succeeded=False,
                failure_code=validation.failure_code,
                safe_message=validation.safe_message,
                final_status=BridgeRunStatus.BLOCKED.value,
            )

        generic_run_id = translated.generic_run_id
        self._append_state_event(generic_run_id, BridgeRunStatus.VALIDATING, "AGY request validated.", 5)
        self._append_state_event(generic_run_id, BridgeRunStatus.PREPARING_SANDBOX, "Preparing managed AGY sandbox.", 15)
        try:
            legacy_run = await asyncio.to_thread(
                self._runner.start_run_in_sandbox,
                run_id=generic_run_id,
                sandbox_root=translated.workspace_root,
                prompt=translated.prompt,
                timeout_seconds=translated.timeout_seconds,
                baseline_root=translated.baseline_root,
            )
        except AntigravityRunnerError as exc:
            return self._start_error_result(generic_run_id, exc)
        except Exception as exc:
            return self._start_error_result(generic_run_id, exc)

        with self._lock:
            self._generic_to_legacy[generic_run_id] = legacy_run.run_id
        observed = legacy_run
        while observed.status not in LEGACY_TERMINAL_STATES and observed.status in LEGACY_AGY_STATE_MAP:
            self._observe_legacy_state(generic_run_id, observed.status)
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                observed = self._runner.get_run(legacy_run.run_id)
            except Exception as exc:
                return self._start_error_result(generic_run_id, exc)
        self._observe_legacy_state(generic_run_id, observed.status)
        return self._map_result(generic_run_id, observed)

    async def cancel(self, run_id: str, reason_code: str = "user_requested") -> BridgeCancellationResult:
        validate_safe_code(reason_code, "reason_code")
        with self._lock:
            terminal = self._terminal_status.get(run_id)
            if terminal is not None:
                return BridgeCancellationResult(
                    BridgeCancellationDisposition.ALREADY_TERMINAL,
                    run_id,
                    requested_at=self._cancellation_requested_at.get(run_id),
                    reason_code=self._cancellation_reason.get(run_id, reason_code),
                )
            if run_id in self._cancellation_requested:
                return BridgeCancellationResult(
                    BridgeCancellationDisposition.ALREADY_REQUESTED,
                    run_id,
                    requested_at=self._cancellation_requested_at.get(run_id),
                    reason_code=self._cancellation_reason.get(run_id, reason_code),
                )
            legacy_run_id = self._generic_to_legacy.get(run_id)
            if legacy_run_id is None:
                return BridgeCancellationResult(BridgeCancellationDisposition.NOT_FOUND, run_id, reason_code=reason_code)
            requested_at = utc_now()
            self._cancellation_requested.add(run_id)
            self._cancellation_requested_at[run_id] = requested_at
            self._cancellation_reason[run_id] = reason_code
        self._append_event(
            run_id,
            BridgeEventType.CANCELLATION_REQUESTED,
            BridgeRunStatus.CANCELLING,
            "AGY cancellation requested.",
        )
        try:
            legacy = await asyncio.to_thread(self._runner.cancel_run, legacy_run_id)
        except AntigravityRunnerError:
            with self._lock:
                self._cancellation_requested.discard(run_id)
                self._cancellation_requested_at.pop(run_id, None)
                self._cancellation_reason.pop(run_id, None)
            return BridgeCancellationResult(BridgeCancellationDisposition.NOT_FOUND, run_id, requested_at, reason_code)
        except Exception:
            with self._lock:
                self._cancellation_requested.discard(run_id)
                self._cancellation_requested_at.pop(run_id, None)
                self._cancellation_reason.pop(run_id, None)
            return BridgeCancellationResult(BridgeCancellationDisposition.REJECTED, run_id, requested_at, reason_code)
        if legacy.status != "cancelled":
            with self._lock:
                self._cancellation_requested.discard(run_id)
                self._cancellation_requested_at.pop(run_id, None)
                self._cancellation_reason.pop(run_id, None)
            return BridgeCancellationResult(BridgeCancellationDisposition.REJECTED, run_id, requested_at, reason_code)
        self._append_terminal_event(
            run_id,
            BridgeRunStatus.CANCELLED,
            BridgeEventType.CANCELLED,
            "AGY run cancelled.",
            BridgeErrorCode.CANCELLED.value,
        )
        return BridgeCancellationResult(
            BridgeCancellationDisposition.ACCEPTED,
            run_id,
            requested_at,
            reason_code,
            escalation=BridgeCancellationEscalation.PROCESS_TERMINATION,
        )

    def events_for_run(self, run_id: str) -> tuple[BridgeRunEvent, ...]:
        with self._lock:
            return tuple(self._events.get(run_id, ()))

    def legacy_run_id(self, generic_run_id: str) -> str | None:
        with self._lock:
            return self._generic_to_legacy.get(generic_run_id)

    def _observe_legacy_state(self, run_id: str, legacy_status: str) -> None:
        with self._lock:
            if self._last_legacy_status.get(run_id) == legacy_status or run_id in self._terminal_status:
                return
            self._last_legacy_status[run_id] = legacy_status
        mapped = map_legacy_agy_state(legacy_status)
        if mapped is BridgeRunStatus.RUNNING:
            self._append_state_event(run_id, mapped, "AGY is running in a managed sandbox.", 40)
        elif mapped is BridgeRunStatus.PREPARING_SANDBOX:
            return

    def _map_result(self, run_id: str, legacy: BridgeSandboxRun) -> BridgeRunResult:
        with self._lock:
            terminal = self._terminal_status.get(run_id)
        if terminal is BridgeRunStatus.CANCELLED or legacy.status == "cancelled":
            self._append_terminal_event(
                run_id,
                BridgeRunStatus.CANCELLED,
                BridgeEventType.CANCELLED,
                "AGY run cancelled.",
                BridgeErrorCode.CANCELLED.value,
            )
            return self._failure_result(legacy, BridgeRunStatus.CANCELLED, BridgeErrorCode.CANCELLED, "AGY run was cancelled.")
        if legacy.status == "failed_timeout":
            self._ensure_running_event(run_id)
            self._append_terminal_event(
                run_id,
                BridgeRunStatus.TIMED_OUT,
                BridgeEventType.FAILURE,
                "AGY run timed out.",
                BridgeErrorCode.TIMEOUT.value,
            )
            return self._failure_result(legacy, BridgeRunStatus.TIMED_OUT, BridgeErrorCode.TIMEOUT, "AGY run timed out.")
        if legacy.status == "completed_no_changes":
            self._ensure_running_event(run_id)
            message = "AGY completed but produced no sandbox changes."
            self._append_terminal_event(
                run_id,
                BridgeRunStatus.FAILED,
                BridgeEventType.FAILURE,
                message,
                BridgeErrorCode.NO_CHANGES_PRODUCED.value,
            )
            return self._failure_result(
                legacy,
                BridgeRunStatus.FAILED,
                BridgeErrorCode.NO_CHANGES_PRODUCED,
                message,
            )
        if legacy.status in {"review_ready", "completed"}:
            self._ensure_running_event(run_id)
            self._append_state_event(run_id, BridgeRunStatus.COLLECTING_ARTIFACTS, "Collecting AGY review artifacts.", 85)
            artifacts = self._artifact_references(run_id, legacy)
            for artifact in artifacts:
                self._append_event(
                    run_id,
                    BridgeEventType.ARTIFACT_CREATED,
                    BridgeRunStatus.COLLECTING_ARTIFACTS,
                    "AGY review artifact created.",
                    artifact_id=artifact.artifact_id,
                )
            self._append_terminal_event(
                run_id,
                BridgeRunStatus.COMPLETED,
                BridgeEventType.COMPLETED,
                "AGY run completed and review artifacts were collected.",
            )
            return BridgeRunResult(
                succeeded=True,
                artifact_ids=tuple(item.artifact_id for item in artifacts),
                artifacts=artifacts,
                safe_message="AGY run completed in a managed sandbox.",
                final_status=BridgeRunStatus.COMPLETED.value,
                review_id=legacy.review_id,
                duration_ms=_duration_ms(legacy),
            )
        mapped = map_legacy_agy_state(legacy.status)
        code = BridgeErrorCode.INTERNAL_ERROR if legacy.status not in LEGACY_AGY_STATE_MAP else BridgeErrorCode.PROCESS_FAILED
        if mapped is BridgeRunStatus.BLOCKED:
            code = BridgeErrorCode.PROVIDER_DISABLED
        if mapped is BridgeRunStatus.INTERRUPTED:
            code = BridgeErrorCode.INTERNAL_ERROR
        self._append_terminal_event(
            run_id,
            mapped if mapped in _GENERIC_TERMINAL else BridgeRunStatus.FAILED,
            BridgeEventType.FAILURE,
            "AGY run failed safely.",
            code.value,
        )
        return self._failure_result(legacy, mapped, code, "AGY run failed safely.")

    def _failure_result(
        self,
        legacy: BridgeSandboxRun,
        status: BridgeRunStatus,
        code: BridgeErrorCode,
        message: str,
    ) -> BridgeRunResult:
        return BridgeRunResult(
            succeeded=False,
            failure_code=code.value,
            safe_message=message,
            final_status=status.value,
            review_id=legacy.review_id,
            duration_ms=_duration_ms(legacy),
        )

    def _artifact_references(self, run_id: str, legacy: BridgeSandboxRun) -> tuple[BridgeArtifactReference, ...]:
        if not legacy.review_id:
            return ()
        review_id = legacy.review_id
        artifact = BridgeArtifactReference(
            artifact_id=review_id,
            run_id=run_id,
            artifact_type=BridgeArtifactType.REVIEW,
            created_at=legacy.completed_at or utc_now(),
            content_hash=hashlib.sha256(review_id.encode("utf-8")).hexdigest(),
            size_bytes=0,
            storage_reference="bridge-reviews.jsonl",
            review_required=True,
            review_id=review_id,
            pipeline_artifact_id=review_id,
        )
        artifact.resolve_internal(self._artifact_root)
        return (artifact,)

    def _start_error_result(self, run_id: str, error: BaseException) -> BridgeRunResult:
        code = _start_error_code(error)
        message = _safe_error_message(code)
        self._append_terminal_event(run_id, BridgeRunStatus.FAILED, BridgeEventType.FAILURE, message, code.value)
        return BridgeRunResult(
            succeeded=False,
            failure_code=code.value,
            safe_message=message,
            final_status=BridgeRunStatus.FAILED.value,
        )

    def _append_state_event(self, run_id: str, status: BridgeRunStatus, message: str, progress: int) -> None:
        self._append_event(run_id, BridgeEventType.STATE_CHANGED, status, message, progress=progress)

    def _ensure_running_event(self, run_id: str) -> None:
        with self._lock:
            has_running = any(item.status == BridgeRunStatus.RUNNING.value for item in self._events.get(run_id, ()))
        if not has_running:
            self._append_state_event(run_id, BridgeRunStatus.RUNNING, "AGY ran in a managed sandbox.", 40)

    def _append_terminal_event(
        self,
        run_id: str,
        status: BridgeRunStatus,
        event_type: BridgeEventType,
        message: str | None,
        failure_code: str | None = None,
    ) -> None:
        with self._lock:
            if run_id in self._terminal_status:
                return
            self._append_event(run_id, event_type, status, message, failure_code=failure_code)
            self._terminal_status.setdefault(run_id, status)

    def _append_event(
        self,
        run_id: str,
        event_type: BridgeEventType,
        status: BridgeRunStatus,
        message: str | None,
        *,
        progress: int | None = None,
        failure_code: str | None = None,
        artifact_id: str | None = None,
    ) -> None:
        with self._lock:
            if run_id in self._terminal_status:
                return
            events = self._events.setdefault(run_id, [])
            sequence = len(events) + 1
            digest = hashlib.sha256(f"{run_id}:{sequence}".encode("utf-8")).hexdigest()[:24]
            events.append(
                BridgeRunEvent(
                    event_id=f"event-{digest}",
                    run_id=run_id,
                    sequence=sequence,
                    timestamp=utc_now(),
                    event_type=event_type,
                    status=status.value,
                    safe_message=sanitize_message(message),
                    progress=progress,
                    failure_code=failure_code,
                    artifact_id=artifact_id,
                )
            )


def _duration_ms(run: BridgeSandboxRun) -> int | None:
    if run.completed_at is None:
        return None
    delta = run.completed_at.astimezone(timezone.utc) - run.started_at.astimezone(timezone.utc)
    return max(0, int(delta.total_seconds() * 1000))


def _start_error_code(error: BaseException) -> BridgeErrorCode:
    if not isinstance(error, AntigravityRunnerError):
        return BridgeErrorCode.INTERNAL_ERROR
    message = str(error).casefold()
    if "disabled" in message:
        return BridgeErrorCode.PROVIDER_DISABLED
    if "not found" in message or "not installed" in message or "cli was not found" in message:
        return BridgeErrorCode.PROVIDER_UNAVAILABLE
    if "workspace" in message or "sandbox" in message:
        return BridgeErrorCode.SANDBOX_INVALID
    return BridgeErrorCode.PROCESS_START_FAILED


def _safe_error_message(code: BridgeErrorCode) -> str:
    return {
        BridgeErrorCode.PROVIDER_DISABLED: "Generic AGY execution is disabled.",
        BridgeErrorCode.PROVIDER_UNAVAILABLE: "AGY CLI is unavailable.",
        BridgeErrorCode.SANDBOX_INVALID: "The managed AGY sandbox is invalid.",
        BridgeErrorCode.PROCESS_START_FAILED: "AGY could not start safely.",
        BridgeErrorCode.INTERNAL_ERROR: "An internal AGY adapter error occurred.",
    }.get(code, "AGY failed safely.")
