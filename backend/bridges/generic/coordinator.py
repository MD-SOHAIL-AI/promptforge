"""Internal generic bridge lifecycle coordinator. No HTTP or subprocess surface."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..sandbox_service import BridgeSandboxError, BridgeSandboxService
from .contracts import BridgeProvider
from .errors import BridgeDomainError, BridgeErrorCode
from .event_transport import InternalBridgeEventTransport
from .models import (
    BridgeArtifactReference,
    BridgeArtifactType,
    BridgeCancellationDisposition,
    BridgeCancellationResult,
    BridgeEventType,
    BridgeRunContext,
    BridgeRunEvent,
    BridgeRunRequest,
    BridgeRunResult,
    BridgeSandboxContext,
)
from .persistence import BridgeRunStore
from .registry import BridgeProviderRegistry
from .routing import DefaultDenyBridgeRoutingPolicy
from .states import TERMINAL_STATES, BridgeRunRecord, BridgeRunStateMachine, BridgeRunStatus
from .validation import sanitize_message, utc_now, validate_safe_code


class BridgeSandboxLifecycle(Protocol):
    def validate(self, context: BridgeSandboxContext) -> BridgeSandboxContext: ...

    def finalize(self, context: BridgeSandboxContext) -> None: ...


class ExistingBridgeSandboxLifecycle:
    """Narrow adapter over the existing sandbox owner; it adds no deletion logic."""

    def __init__(self, service: BridgeSandboxService) -> None:
        self.service = service
        self._finalized: set[str] = set()

    def validate(self, context: BridgeSandboxContext) -> BridgeSandboxContext:
        root = context.internal_sandbox_root.resolve()
        managed = context.managed_sandbox_root.resolve()
        try:
            relative = root.relative_to(managed)
        except ValueError as exc:
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED) from exc
        if len(relative.parts) != 1 or root == context.active_workspace_root.resolve():
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_ESCAPE_BLOCKED)
        if not root.exists() or not root.is_dir():
            raise BridgeDomainError(BridgeErrorCode.SANDBOX_INVALID)
        return context

    def finalize(self, context: BridgeSandboxContext) -> None:
        if context.run_id in self._finalized:
            return
        self._finalized.add(context.run_id)
        if context.cleanup_policy == "cleanup_after_terminal":
            self.service.cleanup_sandbox(run_id=context.internal_sandbox_root.name)


@dataclass(frozen=True, slots=True)
class BridgeArtifactValidator:
    managed_root: Path

    def validate(
        self,
        artifact: BridgeArtifactReference,
        *,
        record: BridgeRunRecord,
        sandbox: BridgeSandboxContext,
    ) -> BridgeArtifactReference:
        if artifact.run_id != record.run_id or sandbox.run_id != record.run_id or sandbox.sandbox_id != record.sandbox_id:
            raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID, "Artifact ownership does not match the run.")
        artifact.resolve_internal(self.managed_root)
        if artifact.artifact_type in {BridgeArtifactType.DIFF, BridgeArtifactType.PATCH, BridgeArtifactType.REVIEW}:
            if not artifact.review_required or not artifact.review_id or not artifact.pipeline_artifact_id:
                raise BridgeDomainError(BridgeErrorCode.ARTIFACT_INVALID)
        return artifact


class GenericBridgeRunCoordinator:
    def __init__(
        self,
        *,
        registry: BridgeProviderRegistry,
        routing_policy: DefaultDenyBridgeRoutingPolicy,
        run_store: BridgeRunStore,
        event_transport: InternalBridgeEventTransport,
        sandbox_lifecycle: BridgeSandboxLifecycle,
        artifact_validator: BridgeArtifactValidator,
        clock: Callable[[], Any] = utc_now,
        event_id_generator: Callable[[str, int], str] | None = None,
    ) -> None:
        self.registry = registry
        self.routing_policy = routing_policy
        self.run_store = run_store
        self.event_transport = event_transport
        self.sandbox_lifecycle = sandbox_lifecycle
        self.artifact_validator = artifact_validator
        self.clock = clock
        self.event_id_generator = event_id_generator or _event_id
        self._state_machine = BridgeRunStateMachine()
        self._owners: set[str] = set()
        self._owner_lock = asyncio.Lock()
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    async def execute(self, request: BridgeRunRequest, sandbox: BridgeSandboxContext) -> BridgeRunRecord:
        existing = self._idempotent_existing(request)
        if existing is not None:
            return existing
        async with self._owner_lock:
            if request.run_id in self._owners:
                return self.run_store.get_record(request.run_id)
            created = self.run_store.create(request)
            self._owners.add(request.run_id)
            self._mutation_locks.setdefault(request.run_id, asyncio.Lock())
        prepared = False
        try:
            await self._emit(created, BridgeEventType.STATE_CHANGED, "Bridge run queued.")
            await self._transition(request.run_id, BridgeRunStatus.VALIDATING, "Bridge request validation started.")
            provider = self._provider_or_none(request.provider_id)
            decision = self.routing_policy.evaluate(provider=provider, request=request, sandbox=sandbox)
            if not decision.allowed or provider is None:
                return await self._transition(
                    request.run_id,
                    BridgeRunStatus.BLOCKED,
                    decision.safe_message or "Bridge routing blocked.",
                    failure_code=decision.failure_code or BridgeErrorCode.PROVIDER_DISABLED.value,
                )
            validation = await provider.validate(request)
            if not validation.valid:
                return await self._transition(
                    request.run_id,
                    BridgeRunStatus.BLOCKED,
                    validation.safe_message or "Provider validation blocked the run.",
                    failure_code=validation.failure_code or BridgeErrorCode.INVALID_REQUEST.value,
                )
            await self._transition(request.run_id, BridgeRunStatus.PREPARING_SANDBOX, "Validating managed sandbox.")
            validated_sandbox = self.sandbox_lifecycle.validate(sandbox)
            prepared = True
            await self._transition(request.run_id, BridgeRunStatus.RUNNING, "Provider started in managed sandbox.")
            # The coordinator owns the hard deadline as a final guard. Providers may
            # enforce a tighter timeout, but no provider bug may leave a run active
            # forever in the product UI.
            result = await asyncio.wait_for(
                provider.start(BridgeRunContext(request, validated_sandbox)),
                timeout=request.timeout_seconds + 5,
            )
            return await self._handle_result(request.run_id, result, validated_sandbox)
        except asyncio.TimeoutError:
            return await self._transition(
                request.run_id,
                BridgeRunStatus.TIMED_OUT,
                "Bridge run exceeded its hard deadline.",
                failure_code=BridgeErrorCode.TIMEOUT.value,
            )
        except BridgeDomainError as exc:
            return await self._fail_if_possible(request.run_id, exc.code.value, exc.safe_message)
        except Exception:
            return await self._fail_if_possible(
                request.run_id,
                BridgeErrorCode.INTERNAL_ERROR.value,
                "An internal bridge coordinator error occurred.",
            )
        finally:
            if prepared:
                await self._finalize_sandbox(request.run_id, sandbox)
            async with self._owner_lock:
                self._owners.discard(request.run_id)

    async def cancel(self, run_id: str, *, reason_code: str = "user_requested") -> BridgeCancellationResult:
        validate_safe_code(reason_code, "reason_code")
        try:
            record = self.run_store.get_record(run_id)
        except BridgeDomainError:
            return BridgeCancellationResult(BridgeCancellationDisposition.NOT_FOUND, run_id, reason_code=reason_code)
        if record.status in TERMINAL_STATES:
            return BridgeCancellationResult(
                BridgeCancellationDisposition.ALREADY_TERMINAL,
                run_id,
                requested_at=record.cancellation_requested_at,
                reason_code=record.cancellation_reason_code or reason_code,
            )
        if record.cancellation_requested:
            return BridgeCancellationResult(
                BridgeCancellationDisposition.ALREADY_REQUESTED,
                run_id,
                requested_at=record.cancellation_requested_at,
                reason_code=record.cancellation_reason_code,
            )
        lock = self._mutation_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current = self.run_store.get_record(run_id)
            if current.status in TERMINAL_STATES:
                return BridgeCancellationResult(
                    BridgeCancellationDisposition.ALREADY_TERMINAL,
                    run_id,
                    requested_at=current.cancellation_requested_at,
                    reason_code=current.cancellation_reason_code or reason_code,
                )
            if current.cancellation_requested:
                return BridgeCancellationResult(
                    BridgeCancellationDisposition.ALREADY_REQUESTED,
                    run_id,
                    requested_at=current.cancellation_requested_at,
                    reason_code=current.cancellation_reason_code,
                )
            updated, accepted = self._state_machine.request_cancellation(current, reason_code=reason_code, at=self.clock())
            stored = self.run_store.upsert_record(updated, expected_revision=current.revision)
            await self._emit(stored, BridgeEventType.CANCELLATION_REQUESTED, "Bridge cancellation requested.")
        provider = self._provider_or_none(record.provider_id)
        if provider is None:
            await self._transition(run_id, BridgeRunStatus.FAILED, "Cancellation owner is unavailable.", failure_code=BridgeErrorCode.PROVIDER_UNAVAILABLE.value)
            return BridgeCancellationResult(BridgeCancellationDisposition.REJECTED, run_id, accepted.requested_at, reason_code)
        result = await provider.cancel(run_id, reason_code)
        if result.disposition in {BridgeCancellationDisposition.ACCEPTED, BridgeCancellationDisposition.ALREADY_REQUESTED}:
            await self._transition(run_id, BridgeRunStatus.CANCELLED, "Bridge run cancelled.", failure_code=BridgeErrorCode.CANCELLED.value)
        elif result.disposition is BridgeCancellationDisposition.ALREADY_TERMINAL:
            pass
        elif result.disposition in {BridgeCancellationDisposition.REJECTED, BridgeCancellationDisposition.NOT_FOUND}:
            await self._transition(run_id, BridgeRunStatus.FAILED, "Provider rejected cancellation.", failure_code=BridgeErrorCode.PROCESS_FAILED.value)
        return result

    async def reconcile_incomplete_runs(self) -> tuple[BridgeRunRecord, ...]:
        before = {item.run_id: item.status for item in self.run_store.list_records()}
        records = self.run_store.reconcile_incomplete_runs(at=self.clock())
        for record in records:
            if before.get(record.run_id) not in TERMINAL_STATES and record.status is BridgeRunStatus.INTERRUPTED:
                await self._emit(record, BridgeEventType.FAILURE, "Bridge run interrupted by backend restart.", failure_code="unexpected_restart")
        return records

    def get_run(self, run_id: str) -> BridgeRunRecord:
        return self.run_store.get_record(run_id)

    def list_recent_runs(self, *, limit: int = 100, offset: int = 0) -> tuple[BridgeRunRecord, ...]:
        bounded = max(1, min(limit, 100))
        records = sorted(self.run_store.list_records(), key=lambda item: (item.created_at, item.run_id), reverse=True)
        return tuple(records[max(0, offset) : max(0, offset) + bounded])

    def list_run_events(self, run_id: str, *, after_sequence: int = 0, limit: int = 500) -> tuple[BridgeRunEvent, ...]:
        return tuple(item for item in self.run_store.list_events(run_id) if item.sequence > after_sequence)[: max(1, min(limit, 500))]

    def list_run_artifacts(self, run_id: str, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        return tuple(item.to_public_dict() for item in self.run_store.list_artifacts(run_id))[: max(1, min(limit, 100))]

    async def _handle_result(
        self,
        run_id: str,
        result: BridgeRunResult,
        sandbox: BridgeSandboxContext,
    ) -> BridgeRunRecord:
        current = self.run_store.get_record(run_id)
        if current.status in TERMINAL_STATES:
            return current
        if current.status is BridgeRunStatus.CANCELLING:
            return await self._transition(run_id, BridgeRunStatus.CANCELLED, "Bridge run cancelled.", failure_code=BridgeErrorCode.CANCELLED.value)
        if not result.succeeded:
            code = result.failure_code or BridgeErrorCode.PROCESS_FAILED.value
            if code == BridgeErrorCode.TIMEOUT.value:
                return await self._transition(run_id, BridgeRunStatus.TIMED_OUT, "Bridge run timed out.", failure_code=code)
            if code == BridgeErrorCode.CANCELLED.value:
                await self._transition(run_id, BridgeRunStatus.CANCELLING, "Provider reported cancellation.")
                return await self._transition(run_id, BridgeRunStatus.CANCELLED, "Bridge run cancelled.", failure_code=code)
            return await self._transition(run_id, BridgeRunStatus.FAILED, result.safe_message or "Bridge provider failed.", failure_code=code)
        await self._transition(run_id, BridgeRunStatus.COLLECTING_ARTIFACTS, "Validating provider artifacts.")
        for artifact in result.artifacts:
            record = self.run_store.get_record(run_id)
            validated = self.artifact_validator.validate(artifact, record=record, sandbox=sandbox)
            self.run_store.add_artifact(validated, managed_root=self.artifact_validator.managed_root)
            await self._emit(
                self.run_store.get_record(run_id),
                BridgeEventType.ARTIFACT_CREATED,
                "Bridge artifact reference validated.",
                artifact_id=validated.artifact_id,
            )
        return await self._transition(run_id, BridgeRunStatus.COMPLETED, "Bridge run completed.")

    async def _transition(
        self,
        run_id: str,
        next_status: BridgeRunStatus,
        message: str,
        *,
        failure_code: str | None = None,
    ) -> BridgeRunRecord:
        lock = self._mutation_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            current = self.run_store.get_record(run_id)
            if current.status in TERMINAL_STATES:
                return current
            transitioned = self._state_machine.transition(
                current,
                next_status,
                at=self.clock(),
                failure_code=failure_code,
                safe_failure_message=message if failure_code else None,
            )
            stored = self.run_store.upsert_record(transitioned, expected_revision=current.revision)
            event_type = _event_type(next_status)
            await self._emit(stored, event_type, message, failure_code=failure_code)
            return self.run_store.get_record(run_id)

    async def _emit(
        self,
        record: BridgeRunRecord,
        event_type: BridgeEventType,
        message: str,
        *,
        failure_code: str | None = None,
        artifact_id: str | None = None,
    ) -> BridgeRunEvent:
        current = self.run_store.get_record(record.run_id)
        sequence = current.event_count + 1
        event = BridgeRunEvent(
            event_id=self.event_id_generator(record.run_id, sequence),
            run_id=record.run_id,
            sequence=sequence,
            timestamp=self.clock(),
            event_type=event_type,
            status=current.status.value,
            safe_message=sanitize_message(message),
            progress=_status_progress(current.status),
            failure_code=failure_code,
            artifact_id=artifact_id,
        )
        self.run_store.append_event(event)
        try:
            published = self.event_transport.publish(event)
            if inspect.isawaitable(published):
                await published
        except Exception:
            current = self.run_store.get_record(record.run_id)
            warning_sequence = current.event_count + 1
            warning = BridgeRunEvent(
                event_id=self.event_id_generator(record.run_id, warning_sequence),
                run_id=record.run_id,
                sequence=warning_sequence,
                timestamp=self.clock(),
                event_type=BridgeEventType.WARNING,
                status=current.status.value,
                safe_message="Internal event delivery failed; persisted replay is required.",
                progress=_status_progress(current.status),
                failure_code=BridgeErrorCode.INTERNAL_ERROR.value,
            )
            self.run_store.append_event(warning)
        return event

    async def _fail_if_possible(self, run_id: str, code: str, message: str) -> BridgeRunRecord:
        current = self.run_store.get_record(run_id)
        if current.status in TERMINAL_STATES:
            return current
        target = BridgeRunStatus.FAILED
        if current.status is BridgeRunStatus.QUEUED:
            target = BridgeRunStatus.FAILED
        return await self._transition(run_id, target, message, failure_code=code)

    async def _finalize_sandbox(self, run_id: str, sandbox: BridgeSandboxContext) -> None:
        try:
            self.sandbox_lifecycle.finalize(sandbox)
        except (BridgeSandboxError, BridgeDomainError, OSError):
            try:
                await self._emit(self.run_store.get_record(run_id), BridgeEventType.WARNING, "Managed sandbox cleanup failed safely.")
            except BridgeDomainError:
                pass

    def _idempotent_existing(self, request: BridgeRunRequest) -> BridgeRunRecord | None:
        for record in self.run_store.list_records():
            if record.run_id == request.run_id or record.correlation_id == request.correlation_id:
                if (
                    record.provider_id == request.provider_id
                    and record.project_id == request.project_id
                    and record.sandbox_id == request.sandbox_id
                    and record.instruction_hash == request.instruction_hash
                ):
                    return record
                raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Duplicate bridge run identity conflicts with existing state.")
        return None

    def _provider_or_none(self, provider_id: str) -> BridgeProvider | None:
        try:
            return self.registry.get(provider_id)
        except BridgeDomainError:
            return None


def _event_id(run_id: str, sequence: int) -> str:
    digest = hashlib.sha256(f"{run_id}:{sequence}:coordinator".encode("utf-8")).hexdigest()[:24]
    return f"event-{digest}"


def _event_type(status: BridgeRunStatus) -> BridgeEventType:
    if status is BridgeRunStatus.COMPLETED:
        return BridgeEventType.COMPLETED
    if status is BridgeRunStatus.CANCELLED:
        return BridgeEventType.CANCELLED
    if status in {BridgeRunStatus.FAILED, BridgeRunStatus.TIMED_OUT, BridgeRunStatus.INTERRUPTED, BridgeRunStatus.BLOCKED}:
        return BridgeEventType.FAILURE
    if status is BridgeRunStatus.CANCELLING:
        return BridgeEventType.CANCELLATION_REQUESTED
    return BridgeEventType.STATE_CHANGED


def _status_progress(status: BridgeRunStatus) -> int:
    return {
        BridgeRunStatus.QUEUED: 5,
        BridgeRunStatus.VALIDATING: 15,
        BridgeRunStatus.PREPARING_SANDBOX: 30,
        BridgeRunStatus.RUNNING: 55,
        BridgeRunStatus.COLLECTING_ARTIFACTS: 85,
        BridgeRunStatus.CANCELLING: 75,
        BridgeRunStatus.COMPLETED: 100,
        BridgeRunStatus.CANCELLED: 100,
        BridgeRunStatus.FAILED: 100,
        BridgeRunStatus.TIMED_OUT: 100,
        BridgeRunStatus.INTERRUPTED: 100,
        BridgeRunStatus.BLOCKED: 100,
    }[status]
