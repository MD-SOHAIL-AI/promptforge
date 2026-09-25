"""Compatibility façade for the canonical ForgeX AgentOrchestrator.

Action-run ownership lives exclusively in :mod:`backend.agent_runtime.orchestrator`.
This façade remains so existing API callers do not need an all-at-once import
migration while the public Agent contract stays stable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .run_models import (
    AUTONOMY_BUILD_ONLY,
    AUTONOMY_BUILD_ONLY_STRICT,
    AUTONOMY_BUILD_THEN_CONFIRM_FLASH,
    AUTONOMY_PLAN_ONLY,
    AUTONOMY_READ_ONLY,
    AUTONOMY_SIMULATE,
    AUTONOMY_STAGED_CHANGES,
    AUTONOMOUS_RUN_AUTONOMIES,
    AUTONOMOUS_STAGE_NAMES,
    TERMINAL_AGENT_STATUSES,
    ProductAgentRun,
)
from .tool_runtime import ProductRuntimeLimits

__all__ = [
    "AUTONOMY_BUILD_ONLY", "AUTONOMY_BUILD_ONLY_STRICT",
    "AUTONOMY_BUILD_THEN_CONFIRM_FLASH", "AUTONOMY_PLAN_ONLY",
    "AUTONOMY_READ_ONLY", "AUTONOMY_SIMULATE", "AUTONOMY_STAGED_CHANGES",
    "AUTONOMOUS_RUN_AUTONOMIES", "AUTONOMOUS_STAGE_NAMES",
    "TERMINAL_AGENT_STATUSES", "ProductAgentRun", "ProductAgentService",
]


class ProductAgentService:
    """Thin compatibility adapter; never owns run lifecycle state."""

    def __init__(
        self,
        *,
        change_service: object,
        provider_registry: object,
        enabled: bool = False,
        limits: ProductRuntimeLimits | None = None,
        summary_store: object | None = None,
        autonomous_workflow_executor: object | None = None,
        flash_confirmation_executor: object | None = None,
        cancel_executor: object | None = None,
    ) -> None:
        del change_service, summary_store, autonomous_workflow_executor, flash_confirmation_executor, cancel_executor
        self.provider_registry = provider_registry
        self.enabled = bool(enabled)
        self.limits = limits or ProductRuntimeLimits()
        self.orchestrator: Any | None = None

    def attach_orchestrator(self, orchestrator: Any) -> None:
        required = (
            "start_run", "get_run", "events", "cancel", "retry", "confirm_flash",
            "close", "steer", "prepare_flash", "apply_pending_changes",
        )
        if not all(callable(getattr(orchestrator, name, None)) for name in required):
            raise ValueError("AGENT_ORCHESTRATOR_INVALID")
        self.orchestrator = orchestrator

    def _owner(self) -> Any:
        if self.orchestrator is None:
            raise RuntimeError("AGENT_ORCHESTRATOR_REQUIRED")
        return self.orchestrator

    async def start_run(
        self,
        *,
        project_id: str,
        active_workspace_root: str | Path,
        instruction: str,
        provider_id: str = "fake_planner",
        model_id: str | None = None,
        fallback_provider_id: str | None = None,
        autonomy: str = AUTONOMY_STAGED_CHANGES,
        active_workspace: Mapping[str, object] | None = None,
        board_port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool = False,
        session_id: str | None = None,
        explicit_edit_authorized: bool = False,
    ) -> ProductAgentRun:
        if not self.enabled:
            raise PermissionError("AGENT_RUNTIME_DISABLED")
        del fallback_provider_id
        return await self._owner().start_run(
            project_id=project_id,
            active_workspace_root=active_workspace_root,
            instruction=instruction,
            provider_id=provider_id,
            model_id=model_id,
            fallback_provider_id=None,
            autonomy=autonomy,
            active_workspace=active_workspace or {},
            board_port=board_port,
            board_type=board_type,
            environment=environment,
            start_monitor_after_flash=start_monitor_after_flash,
            session_id=session_id,
            explicit_edit_authorized=explicit_edit_authorized,
        )

    def get_run(self, run_id: str) -> ProductAgentRun:
        return self._owner().get_run(run_id)

    def steer(self, run_id: str, message: str) -> ProductAgentRun:
        return self._owner().steer(run_id, message)

    def prepare_flash(self, run_id: str, **kwargs: object) -> ProductAgentRun:
        return self._owner().prepare_flash(run_id, **kwargs)

    async def confirm_flash(self, run_id: str, **kwargs: object) -> ProductAgentRun:
        return await self._owner().confirm_flash(run_id, **kwargs)

    def apply_pending_changes(self, run_id: str) -> ProductAgentRun:
        return self._owner().apply_pending_changes(run_id)

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        return self._owner().events(run_id, after_sequence=after_sequence)

    def session_projection(self, session_id: str) -> dict[str, object]:
        return self._owner().session_projection(session_id)

    def transient_events(self, run_id: str, *, after_transient_sequence: int = 0) -> tuple[dict[str, object], ...]:
        return self._owner().transient_events(run_id, after_transient_sequence=after_transient_sequence)

    def queued_guidance(self, run_id: str) -> tuple[dict[str, object], ...]:
        return self._owner().queued_guidance(run_id)

    def edit_guidance(self, run_id: str, item_id: str, message: str) -> dict[str, object]:
        return self._owner().edit_guidance(run_id, item_id, message)

    def remove_guidance(self, run_id: str, item_id: str) -> dict[str, object]:
        return self._owner().remove_guidance(run_id, item_id)

    async def cancel(self, run_id: str) -> ProductAgentRun:
        return await self._owner().cancel(run_id)

    async def retry(self, run_id: str) -> ProductAgentRun:
        return await self._owner().retry(run_id)

    async def close(self) -> None:
        if self.orchestrator is not None:
            await self.orchestrator.close()
