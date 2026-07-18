"""Experimental generation-to-review adapter for the unified coding workflow.

Generation succeeds by creating a review, not by changing the active workspace.
This adapter deliberately has no apply, build, flash, or monitor dependencies.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ...agent_runtime.api_coding_agent_service import FAKE_API_CODING_AGENT_PROVIDER_ID, ApiCodingAgentService
from ...agent_runtime.coding_provider_contracts import (
    CodingContextMode,
    CodingProviderCapability,
    CodingProviderContractError,
    CodingProviderEvent,
    CodingProviderFailureCode,
    CodingProviderRunRequest,
    CodingProviderSelectionRequest,
    CodingRunStage,
    CodingRunStatus,
)
from ...agent_runtime.coding_provider_registry import CodingProviderRegistry, CodingProviderRegistryError, default_coding_provider_registry
from ...agent_runtime.coding_workflow_store import (
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)
from ...agent_runtime.fake_api_coding_provider_adapter import FakeApiCodingProviderAdapter, run_coding_provider


UNIFIED_CODING_WORKFLOW_FLAG = "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW"


class CodingAgentWorkflowGenerationStatus(str, Enum):
    REVIEW_CREATED = "review_created"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CodingAgentWorkflowResult:
    run_id: str
    task_id: str
    project_id: str
    provider_id: str
    provider_type: str
    status: CodingRunStatus
    generation_status: CodingAgentWorkflowGenerationStatus
    summary: str = ""
    review_id: str | None = None
    files_changed: tuple[str, ...] = ()
    events: tuple[CodingProviderEvent, ...] = ()
    failure_code: CodingProviderFailureCode | None = None
    safe_message: str = ""
    downstream_stages_started: bool = False

    def __post_init__(self) -> None:
        if self.downstream_stages_started:
            raise ValueError("coding agent workflow result cannot start downstream stages")
        success = self.status is CodingRunStatus.AWAITING_APPLY
        if success and (self.generation_status is not CodingAgentWorkflowGenerationStatus.REVIEW_CREATED or not self.review_id):
            raise ValueError("awaiting_apply requires a created review")
        if not success and self.status is not CodingRunStatus.FAILED:
            raise ValueError("workflow adapter result must be awaiting_apply or failed")
        if self.status is CodingRunStatus.FAILED and self.failure_code is None:
            raise ValueError("failed workflow adapter result requires failure_code")

    @property
    def review_created(self) -> bool:
        return self.generation_status is CodingAgentWorkflowGenerationStatus.REVIEW_CREATED

    @property
    def awaiting_apply(self) -> bool:
        return self.status is CodingRunStatus.AWAITING_APPLY

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "status": self.status.value,
            "generation_status": self.generation_status.value,
            "summary": self.summary,
            "review_id": self.review_id,
            "files_changed": list(self.files_changed),
            "events": [event.to_safe_dict() for event in self.events],
            "failure_code": self.failure_code.value if self.failure_code else None,
            "safe_message": self.safe_message,
            "review_created": self.review_created,
            "awaiting_apply": self.awaiting_apply,
            "downstream_stages_started": False,
            "next_action": "await_user_approval" if self.awaiting_apply else None,
        }


class CodingAgentGenerationAdapter:
    """Feature-gated fake provider selection and review-gate handoff."""

    def __init__(
        self,
        *,
        fake_service: ApiCodingAgentService,
        store: CodingWorkflowStore,
        env: Mapping[str, str] | None = None,
        registry: CodingProviderRegistry | None = None,
    ) -> None:
        if not isinstance(fake_service, ApiCodingAgentService):
            raise TypeError("fake_service must be an ApiCodingAgentService")
        if not isinstance(store, CodingWorkflowStore):
            raise TypeError("store must be a CodingWorkflowStore")
        source = os.environ if env is None else env
        self._env = dict(source)
        self._enabled = self._env.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"
        self._fake_enabled = self._env.get("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "").strip() == "1"
        self._registry = registry
        self._fake_adapter = FakeApiCodingProviderAdapter(fake_service)
        self._store = store

    def generate_review(
        self,
        prompt: str,
        workspace_path: Path,
        *,
        explicit_provider_id: str | None = None,
        context_mode: CodingContextMode = CodingContextMode.SELECTED_FILES,
    ) -> CodingAgentWorkflowResult:
        run_id = f"coding-workflow-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"
        project_id = f"project-{uuid.uuid4().hex}"
        provider_id = explicit_provider_id or FAKE_API_CODING_AGENT_PROVIDER_ID
        provider_type = "api_coding_agent" if provider_id == FAKE_API_CODING_AGENT_PROVIDER_ID else "unknown"

        def fail(code: CodingProviderFailureCode, message: str, *, selected_type: str = provider_type) -> CodingAgentWorkflowResult:
            return self._persist(_workflow_failure(
                run_id,
                task_id,
                project_id,
                provider_id,
                selected_type,
                code,
                message,
            ))

        if not self._enabled:
            return fail(
                CodingProviderFailureCode.UNIFIED_CODING_WORKFLOW_DISABLED,
                "Unified coding workflow is disabled.",
            )
        if not isinstance(workspace_path, Path):
            return fail(
                CodingProviderFailureCode.INVALID_REQUEST,
                "Workspace path must be a Path.",
            )

        registry = self._registry or default_coding_provider_registry(self._env)
        if provider_id != FAKE_API_CODING_AGENT_PROVIDER_ID:
            try:
                unsupported = registry.get(provider_id)
            except CodingProviderRegistryError:
                return fail(
                    CodingProviderFailureCode.PROVIDER_NOT_FOUND,
                    "The explicitly selected coding provider is not registered.",
                )
            return fail(
                CodingProviderFailureCode.PROVIDER_NOT_IMPLEMENTED,
                "Only the dev-only fake API coding provider is implemented for this workflow phase.",
                selected_type=unsupported.provider_type.value,
            )
        if not self._fake_enabled:
            return fail(
                CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED,
                "Fake API coding agent is disabled.",
            )

        required_capabilities = frozenset({
            CodingProviderCapability.GENERATE_FILES,
            CodingProviderCapability.CREATE_REVIEW,
        })
        try:
            selection = registry.select(CodingProviderSelectionRequest(
                prompt=prompt,
                explicit_provider_id=provider_id,
                required_capabilities=required_capabilities,
                context_mode=context_mode,
                allow_dev_providers=True,
            ))
        except CodingProviderContractError:
            return fail(
                CodingProviderFailureCode.INVALID_REQUEST,
                "Coding workflow selection request was invalid.",
            )
        if not selection.selected:
            failure_code = selection.failure_code or CodingProviderFailureCode.PROVIDER_UNAVAILABLE
            if failure_code is CodingProviderFailureCode.PROVIDER_DISABLED:
                failure_code = CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
            return fail(failure_code, selection.safe_message)

        request = CodingProviderRunRequest(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            provider_id=provider_id,
            prompt=prompt,
            workspace_root_display=workspace_path.name or "workspace",
            context_mode=context_mode,
            requested_capabilities=required_capabilities,
            metadata={"workflow_adapter": "experimental_fake_review_v1"},
        )
        provider_result = run_coding_provider(
            registry,
            request,
            workspace_path,
            fake_adapter=self._fake_adapter,
        )
        if provider_result.status is not CodingRunStatus.AWAITING_APPLY or not provider_result.review_id:
            return self._persist(CodingAgentWorkflowResult(
                run_id=run_id,
                task_id=task_id,
                project_id=project_id,
                provider_id=provider_id,
                provider_type="api_coding_agent",
                status=CodingRunStatus.FAILED,
                generation_status=CodingAgentWorkflowGenerationStatus.FAILED,
                events=provider_result.events,
                failure_code=provider_result.failure_code or CodingProviderFailureCode.PROVIDER_FAILED,
                safe_message=provider_result.safe_message,
            ))

        gate_event = _event(
            run_id,
            provider_id,
            provider_result.events[-1].sequence + 1,
            "apply.waiting_for_approval",
            "Generation created a review. Explicit approval is required before apply.",
        )
        return self._persist(CodingAgentWorkflowResult(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            provider_id=provider_id,
            provider_type="api_coding_agent",
            status=CodingRunStatus.AWAITING_APPLY,
            generation_status=CodingAgentWorkflowGenerationStatus.REVIEW_CREATED,
            summary=provider_result.summary,
            review_id=provider_result.review_id,
            files_changed=provider_result.files_changed,
            events=provider_result.events + (gate_event,),
            safe_message="Generation stage created a ForgeX review and paused before apply.",
        ))

    def _persist(self, result: CodingAgentWorkflowResult) -> CodingAgentWorkflowResult:
        now = _utc_now()
        record = CodingWorkflowRunRecord(
            run_id=result.run_id,
            task_id=result.task_id,
            project_id=result.project_id,
            provider_id=result.provider_id,
            provider_type=result.provider_type,
            status=result.status.value,
            generation_status=result.generation_status.value,
            review_id=result.review_id,
            next_action="await_user_approval" if result.awaiting_apply else None,
            files_changed=result.files_changed,
            created_at=now,
            updated_at=now,
            safe_summary=result.summary or None,
            failure_code=result.failure_code.value if result.failure_code else None,
            safe_message=result.safe_message or None,
            metadata={"experimental_workflow": True, "downstream_stages_started": False},
        )
        events = tuple(CodingWorkflowEventRecord(
            event_id=event.event_id,
            run_id=event.run_id,
            sequence=event.sequence,
            event_type=event.event_type,
            stage=event.stage.value,
            status=event.status.value,
            safe_message=event.safe_message,
            created_at=event.timestamp,
            metadata=event.metadata,
        ) for event in result.events)
        try:
            self._store.persist_run(record, events)
        except CodingWorkflowStoreError as exc:
            failure_code = (
                CodingProviderFailureCode.LEGACY_WORKFLOW_ADMISSION_DISABLED
                if exc.code == "LEGACY_WORKFLOW_ADMISSION_DISABLED"
                else CodingProviderFailureCode.WORKFLOW_PERSISTENCE_FAILED
            )
            return CodingAgentWorkflowResult(
                run_id=result.run_id,
                task_id=result.task_id,
                project_id=result.project_id,
                provider_id=result.provider_id,
                provider_type=result.provider_type,
                status=CodingRunStatus.FAILED,
                generation_status=CodingAgentWorkflowGenerationStatus.FAILED,
                summary=result.summary,
                review_id=result.review_id,
                files_changed=result.files_changed,
                events=result.events,
                failure_code=failure_code,
                safe_message=(
                    "New legacy workflow admission is disabled; use the durable workflow service."
                    if failure_code is CodingProviderFailureCode.LEGACY_WORKFLOW_ADMISSION_DISABLED
                    else "Coding workflow state could not be persisted safely."
                ),
            )
        return result


def _workflow_failure(
    run_id: str,
    task_id: str,
    project_id: str,
    provider_id: str,
    provider_type: str,
    failure_code: CodingProviderFailureCode,
    message: str,
) -> CodingAgentWorkflowResult:
    return CodingAgentWorkflowResult(
        run_id=run_id,
        task_id=task_id,
        project_id=project_id,
        provider_id=provider_id,
        provider_type=provider_type,
        status=CodingRunStatus.FAILED,
        generation_status=CodingAgentWorkflowGenerationStatus.FAILED,
        events=(_event(run_id, provider_id, 1, "generation.failed", message, status=CodingRunStatus.FAILED),),
        failure_code=failure_code,
        safe_message=message,
    )


def _event(
    run_id: str,
    provider_id: str,
    sequence: int,
    event_type: str,
    message: str,
    *,
    status: CodingRunStatus = CodingRunStatus.AWAITING_APPLY,
) -> CodingProviderEvent:
    digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
    return CodingProviderEvent(
        event_id=f"event-{digest}",
        sequence=sequence,
        run_id=run_id,
        event_type=event_type,
        stage=CodingRunStage.REVIEW if status is CodingRunStatus.AWAITING_APPLY else CodingRunStage.GENERATION,
        status=status,
        safe_message=message,
        metadata={"provider_id": provider_id, "experimental_workflow": True},
    )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
