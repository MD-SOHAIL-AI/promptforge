"""Compatibility routing for the existing AGY API surface."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .audit_log import BridgeAuditLog, hash_workspace_root
from .agy_trusted_workspace import AGYTrustedWorkspaceLease, AGYTrustedWorkspaceService
from .diff_service import BridgeDiffError, BridgeDiffService
from .generic import BridgeRunRequest, BridgeRunStatus, BridgeSandboxContext, GenericBridgeRunCoordinator
from .generic.errors import BridgeDomainError
from .providers.antigravity_runner import AGY_PROVIDER_ID, AntigravityRunnerError, AntigravitySandboxRunner
from .review_models import BridgeAuditEntry
from .run_models import BridgeSandboxRun
from .sandbox_service import BridgeSandboxError, BridgeSandboxService


class AGYExecutionMode(str, Enum):
    LEGACY = "legacy"
    GENERIC = "generic"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class AGYCutoverFlags:
    legacy_enabled: bool = False
    generic_routing_enabled: bool = False
    generic_provider_enabled: bool = False
    cutover_enabled: bool = False

    @classmethod
    def from_environment(cls, env: dict[str, str] | None = None) -> "AGYCutoverFlags":
        source = env if env is not None else os.environ
        enabled = lambda name: source.get(name, "").strip() == "1"
        return cls(
            legacy_enabled=enabled("FORGEX_ENABLE_AGY_BRIDGE"),
            generic_routing_enabled=enabled("FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING"),
            generic_provider_enabled=enabled("FORGEX_ENABLE_AGY_GENERIC_PROVIDER"),
            cutover_enabled=enabled("FORGEX_ENABLE_AGY_GENERIC_CUTOVER"),
        )

    def effective_mode(self) -> AGYExecutionMode:
        if not self.legacy_enabled:
            return AGYExecutionMode.BLOCKED
        if not self.cutover_enabled:
            return AGYExecutionMode.LEGACY
        if self.generic_routing_enabled and self.generic_provider_enabled:
            return AGYExecutionMode.GENERIC
        return AGYExecutionMode.BLOCKED


CANONICAL_TO_LEGACY_STATUS = {
    BridgeRunStatus.QUEUED: "pending",
    BridgeRunStatus.VALIDATING: "pending",
    BridgeRunStatus.PREPARING_SANDBOX: "pending",
    BridgeRunStatus.RUNNING: "running",
    BridgeRunStatus.COLLECTING_ARTIFACTS: "running",
    BridgeRunStatus.COMPLETED: "review_ready",
    BridgeRunStatus.BLOCKED: "failed",
    BridgeRunStatus.FAILED: "failed",
    BridgeRunStatus.CANCELLING: "running",
    BridgeRunStatus.CANCELLED: "cancelled",
    BridgeRunStatus.TIMED_OUT: "failed_timeout",
    BridgeRunStatus.INTERRUPTED: "failed",
}


def legacy_status_for(status: BridgeRunStatus | str) -> str:
    try:
        canonical = status if isinstance(status, BridgeRunStatus) else BridgeRunStatus(status)
    except ValueError:
        return "failed"
    return CANONICAL_TO_LEGACY_STATUS.get(canonical, "failed")


class AGYExecutionRouter:
    """Select exactly one path before sandbox or provider work starts."""

    def __init__(
        self,
        *,
        flags: AGYCutoverFlags,
        legacy_runner: AntigravitySandboxRunner,
        generic_coordinator: GenericBridgeRunCoordinator,
        sandbox_service: BridgeSandboxService,
        review_service: BridgeDiffService,
        audit_log: BridgeAuditLog | None = None,
        trusted_workspace_service: AGYTrustedWorkspaceService | None = None,
    ) -> None:
        self.flags = flags
        self.legacy_runner = legacy_runner
        self.generic_coordinator = generic_coordinator
        self.sandbox_service = sandbox_service
        self.review_service = review_service
        self.audit_log = audit_log
        self.trusted_workspace_service = trusted_workspace_service
        self._trusted_leases: dict[str, AGYTrustedWorkspaceLease] = {}
        self._runs: dict[str, BridgeSandboxRun] = {}
        self._modes: dict[str, AGYExecutionMode] = {}
        self._idempotency: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.generic_restore_status = "ready"
        try:
            self._restore_generic_runs()
        except BridgeDomainError:
            # Corrupt persisted generic metadata disables generic history, but
            # must not prevent the packaged backend from serving health and
            # safety diagnostics.
            self.generic_restore_status = "unavailable"

    @property
    def effective_mode(self) -> AGYExecutionMode:
        return self.flags.effective_mode()

    async def start_run(
        self,
        *,
        workspace_root: str | Path,
        prompt: str,
        timeout_seconds: int,
        idempotency_key: str | None = None,
        project_id: str | None = None,
    ) -> BridgeSandboxRun:
        mode = self.effective_mode
        if mode is AGYExecutionMode.BLOCKED:
            raise AntigravityRunnerError("AGY execution is blocked by the compatibility routing policy.")
        if idempotency_key and idempotency_key in self._idempotency:
            return self.get_run(self._idempotency[idempotency_key])
        if mode is AGYExecutionMode.LEGACY:
            run = self.legacy_runner.start_run(
                workspace_root=workspace_root,
                prompt=prompt,
                timeout_seconds=timeout_seconds,
            )
            self._remember(run, mode, idempotency_key)
            self._audit("agy_execution_routed", run, mode)
            return run

        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise AntigravityRunnerError("Workspace root must be an existing directory.")
        run_id = f"bridge-run-{uuid.uuid4().hex}"
        correlation = idempotency_key or f"correlation-{uuid.uuid4().hex}"
        sandbox_id = f"sandbox-{hashlib.sha256(run_id.encode()).hexdigest()[:16]}"
        trusted = self.trusted_workspace_service
        if trusted is not None and trusted.is_enabled():
            lease = trusted.acquire_and_reset(root)
            sandbox_root = lease.workspace_root
            managed_sandbox_root = trusted.managed_root
            self._trusted_leases[run_id] = lease
        else:
            sandbox_root = self.sandbox_service.create_sandbox(run_id=run_id, workspace_root=root)
            managed_sandbox_root = self.sandbox_service.sandbox_root
        request = BridgeRunRequest(
            run_id=run_id,
            provider_id="agy",
            project_id=project_id or f"project-{hashlib.sha256(str(root).encode()).hexdigest()[:16]}",
            sandbox_id=sandbox_id,
            instruction=prompt,
            requested_capability="edit_files",
            timeout_seconds=max(10, timeout_seconds),
            correlation_id=correlation,
            execution_mode="generic",
        )
        context = BridgeSandboxContext(
            sandbox_id=sandbox_id,
            run_id=run_id,
            project_id=request.project_id,
            creation_status="ready",
            cleanup_policy="retain_for_review",
            containment_verified=True,
            internal_sandbox_root=sandbox_root,
            managed_sandbox_root=managed_sandbox_root,
            active_workspace_root=root,
        )
        run = BridgeSandboxRun(
            provider_id=AGY_PROVIDER_ID,
            workspace_root_hash=hash_workspace_root(str(root)),
            sandbox_root="",
            run_id=run_id,
        )
        self._remember(run, mode, idempotency_key)
        self._audit("agy_execution_routed", run, mode, generic_run_id=run_id)
        self._tasks[run_id] = asyncio.create_task(self._execute_generic(request, context))
        return run

    def get_run(self, run_id: str) -> BridgeSandboxRun:
        mode = self._modes.get(run_id)
        if mode is AGYExecutionMode.GENERIC:
            return self._refresh_generic(run_id)
        if mode is AGYExecutionMode.LEGACY:
            return self.legacy_runner.get_run(run_id)
        try:
            record = self.generic_coordinator.get_run(run_id)
        except Exception:
            return self.legacy_runner.get_run(run_id)
        if record.execution_mode == "generic" and record.provider_id == "agy":
            self._modes[run_id] = AGYExecutionMode.GENERIC
            return self._refresh_generic(run_id)
        return self.legacy_runner.get_run(run_id)

    def list_runs(self) -> tuple[BridgeSandboxRun, ...]:
        combined = {run.run_id: run for run in self.legacy_runner.list_runs()}
        for run_id in tuple(self._runs):
            combined[run_id] = self.get_run(run_id)
        return tuple(sorted(combined.values(), key=lambda item: item.started_at, reverse=True))

    async def cancel_run(self, run_id: str) -> BridgeSandboxRun:
        mode = self._modes.get(run_id)
        if mode is None:
            self.get_run(run_id)
            mode = self._modes.get(run_id, AGYExecutionMode.LEGACY)
        if mode is AGYExecutionMode.LEGACY:
            return self.legacy_runner.cancel_run(run_id)
        await self.generic_coordinator.cancel(run_id)
        return self._refresh_generic(run_id)

    async def _execute_generic(self, request: BridgeRunRequest, context: BridgeSandboxContext) -> None:
        run = self._runs[request.run_id]
        try:
            record = await self.generic_coordinator.execute(request, context)
            self._apply_record(run, record)
        except Exception:
            run.status = "failed"
            run.error_message = "Generic AGY execution failed safely."
            run.completed_at = datetime.now(timezone.utc)
            try:
                self.sandbox_service.cleanup_sandbox(run_id=request.run_id)
            except (BridgeSandboxError, OSError):
                pass
        finally:
            self._audit(
                "agy_execution_terminal",
                run,
                AGYExecutionMode.GENERIC,
                generic_run_id=request.run_id,
                failure_code=self._failure_code(request.run_id),
            )
            self._tasks.pop(request.run_id, None)
            lease = self._trusted_leases.pop(request.run_id, None)
            if lease is not None and self.trusted_workspace_service is not None:
                try:
                    self.trusted_workspace_service.release(lease)
                except BridgeSandboxError:
                    pass

    def _refresh_generic(self, run_id: str) -> BridgeSandboxRun:
        run = self._runs.get(run_id)
        record = self.generic_coordinator.get_run(run_id)
        if run is None:
            run = BridgeSandboxRun(
                provider_id=AGY_PROVIDER_ID,
                workspace_root_hash="",
                sandbox_root="",
                run_id=run_id,
                started_at=record.created_at,
            )
            self._runs[run_id] = run
        self._apply_record(run, record)
        return run

    def _apply_record(self, run: BridgeSandboxRun, record) -> None:
        run.status = legacy_status_for(record.status)  # type: ignore[assignment]
        run.completed_at = record.finished_at
        run.error_message = record.safe_failure_message
        artifacts = self.generic_coordinator.list_run_artifacts(record.run_id)
        review_id = next((item.get("review_id") for item in artifacts if item.get("review_id")), None)
        if isinstance(review_id, str):
            run.review_id = review_id
            try:
                run.changed_file_count = len(self.review_service.get_review(review_id).changed_files)
            except BridgeDiffError:
                pass

    def _remember(self, run: BridgeSandboxRun, mode: AGYExecutionMode, key: str | None) -> None:
        self._runs[run.run_id] = run
        self._modes[run.run_id] = mode
        if key:
            self._idempotency[key] = run.run_id

    def _restore_generic_runs(self) -> None:
        for record in self.generic_coordinator.list_recent_runs(limit=100):
            if record.provider_id == "agy" and record.execution_mode == "generic":
                self._modes[record.run_id] = AGYExecutionMode.GENERIC
                self._idempotency.setdefault(record.correlation_id, record.run_id)

    def _failure_code(self, run_id: str) -> str | None:
        try:
            return self.generic_coordinator.get_run(run_id).failure_code
        except Exception:
            return "internal_error"

    def _audit(
        self,
        event: str,
        run: BridgeSandboxRun,
        mode: AGYExecutionMode,
        *,
        generic_run_id: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id="agy",
                workspace_root_hash=run.workspace_root_hash,
                changed_file_count=run.changed_file_count,
                approved=False,
                review_id=run.review_id or run.run_id,
                metadata={
                    "execution_mode": mode.value,
                    "generic_run_id": generic_run_id,
                    "terminal_status": run.status if run.completed_at else None,
                    "failure_code": failure_code,
                },
            )
        )
