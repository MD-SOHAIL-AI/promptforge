"""Review-only repair generation for failed unified coding workflows."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .api_coding_agent_service import ApiCodingAgentService
from .coding_provider_contracts import CodingProviderRunResult
from .coding_workflow_service import UNIFIED_CODING_WORKFLOW_FLAG
from .coding_workflow_store import (
    RUN_NOT_FOUND,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)
from .real_api_coding_provider_adapter import (
    REAL_API_CODING_AGENT_FLAG,
    REAL_API_CODING_AGENT_PROVIDER_ID,
    RealApiCodingProviderAdapter,
)


CODING_AGENT_REPAIR_LOOP_FLAG = "FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP"
REPAIR_DISABLED = "CODING_WORKFLOW_REPAIR_DISABLED"
REPAIR_NOT_ALLOWED = "CODING_WORKFLOW_REPAIR_NOT_ALLOWED"
REPAIR_REQUIRES_BUILD_FAILURE = "CODING_WORKFLOW_REPAIR_REQUIRES_BUILD_FAILURE"
REPAIR_LIMIT_EXCEEDED = "CODING_WORKFLOW_REPAIR_LIMIT_EXCEEDED"
REPAIR_WORKSPACE_NOT_FOUND = "CODING_WORKFLOW_WORKSPACE_NOT_FOUND"
REPAIR_PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"
MAX_REPAIR_ATTEMPTS = 2
MAX_BUILD_ERROR_CHARS = 8_000
MAX_ERROR_LINES = 120

_SECRET_PATTERNS = (
    re.compile(r"(?i)(openai_api_key|api[_-]?key|authorization:\s*bearer|password|token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)-----begin [a-z0-9 ]*private key-----"),
)
_WINDOWS_ABS_RE = re.compile(r"(?i)\b[a-z]:\\[^\s\"'<>]+")
_UNC_RE = re.compile(r"\\\\[^\s\"'<>]+\\[^\s\"'<>]+")
_POSIX_PRIVATE_RE = re.compile(r"(?<!\w)/(?:Users|home|var|tmp)/[^\s\"'<>]+")


class ModelRouterLike(Protocol):
    async def generate_model(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True)
class CodingWorkflowRepairResult:
    run_id: str
    status: str
    generation_status: str | None = None
    repair_of_run_id: str | None = None
    review_id: str | None = None
    next_action: str | None = None
    files_changed: tuple[str, ...] = ()
    failure_code: str | None = None
    safe_message: str = ""
    events: tuple[CodingWorkflowEventRecord, ...] = ()
    metadata: Mapping[str, object] | None = None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "generation_status": self.generation_status,
            "repair_of_run_id": self.repair_of_run_id,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "files_changed": list(self.files_changed),
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
            "events": [event.to_dict() for event in self.events],
            "metadata": dict(self.metadata or {}),
        }


class CodingWorkflowRepairService:
    def __init__(
        self,
        *,
        store: CodingWorkflowStore,
        model_router: ModelRouterLike,
        review_service: ApiCodingAgentService,
        env: Mapping[str, str] | None = None,
        max_repair_attempts: int = MAX_REPAIR_ATTEMPTS,
    ) -> None:
        self._store = store
        self._model_router = model_router
        self._review_service = review_service
        self._env = os.environ if env is None else env
        self._max_repair_attempts = max(0, min(10, int(max_repair_attempts)))

    async def generate_build_repair_review(
        self,
        run_id: str,
        *,
        workspace_path: Path,
        provider_id: str | None = None,
        model: str | None = None,
        selected_files: list[str] | None = None,
    ) -> CodingWorkflowRepairResult:
        if self._env.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() != "1":
            return _rejected(run_id, "rejected", "UNIFIED_CODING_WORKFLOW_DISABLED", "Unified coding workflow is disabled.")
        if self._env.get(REAL_API_CODING_AGENT_FLAG, "").strip() != "1":
            return _rejected(run_id, "rejected", "REAL_API_CODING_AGENT_DISABLED", "Real API coding agent is disabled.")
        if self._env.get(CODING_AGENT_REPAIR_LOOP_FLAG, "").strip() != "1":
            return _rejected(run_id, "rejected", REPAIR_DISABLED, "Build-failure repair loop is disabled.")

        try:
            parent = self._store.get_run(run_id)
            parent_events = self._store.list_events(run_id)
        except CodingWorkflowStoreError as exc:
            code = RUN_NOT_FOUND if exc.code == RUN_NOT_FOUND else REPAIR_PERSISTENCE_FAILED
            message = "Coding workflow run was not found." if code == RUN_NOT_FOUND else "Coding workflow state could not be read safely."
            return _rejected(run_id, "rejected", code, message)

        eligibility_error = self._eligibility_error(parent, parent_events)
        if eligibility_error is not None:
            return _rejected(run_id, parent.status, eligibility_error[0], eligibility_error[1])
        if self._repair_attempt_count(parent.run_id) >= self._max_repair_attempts:
            return _rejected(parent.run_id, parent.status, REPAIR_LIMIT_EXCEEDED, "Build repair attempt limit has been reached.")

        root = Path(workspace_path).expanduser().resolve()
        if not root.is_dir():
            return _rejected(parent.run_id, parent.status, REPAIR_WORKSPACE_NOT_FOUND, "Repair workspace was not found.")

        attempt = self._repair_attempt_count(parent.run_id) + 1
        repair_run_id = f"coding-workflow-repair-{uuid.uuid4().hex}"
        prompt = _repair_prompt(parent, parent_events, attempt)
        context_files = _repair_selected_files(parent, selected_files)
        initial_metadata = {
            "repair_of_run_id": parent.run_id,
            "repair_attempt_number": attempt,
            "repair_reason": "build_failed",
            "parent_review_id": parent.review_id,
            "parent_files_changed": list(parent.files_changed),
            "build_error_summary_chars": len(_build_error_summary(parent, parent_events)),
        }
        try:
            _persist_repair_started(self._store, repair_run_id, parent=parent, attempt=attempt, metadata=initial_metadata)
        except CodingWorkflowStoreError:
            return _rejected(parent.run_id, parent.status, REPAIR_PERSISTENCE_FAILED, "Repair start state could not be persisted safely.")

        result = await RealApiCodingProviderAdapter(
            model_router=self._model_router,
            review_service=self._review_service,
            env={**dict(self._env), REAL_API_CODING_AGENT_FLAG: "1"},
        ).generate_review(
            prompt=prompt,
            workspace_path=root,
            context_mode="selected_files",
            selected_files=context_files,
            provider_id=provider_id,
            model_route=model,
            run_id=repair_run_id,
        )

        metadata = {
            **initial_metadata,
            **_generation_metadata(result.metadata),
        }
        try:
            record, events = _persist_repair_result(
                self._store,
                result,
                parent=parent,
                attempt=attempt,
                metadata=metadata,
            )
        except CodingWorkflowStoreError:
            return _rejected(parent.run_id, parent.status, REPAIR_PERSISTENCE_FAILED, "Repair review state could not be persisted safely.")

        if result.failure_code is not None:
            return CodingWorkflowRepairResult(
                run_id=record.run_id,
                status=record.status,
                generation_status=record.generation_status,
                repair_of_run_id=parent.run_id,
                review_id=record.review_id,
                next_action=record.next_action,
                files_changed=record.files_changed,
                failure_code=result.failure_code.value,
                safe_message=result.safe_message,
                events=events,
                metadata=record.metadata,
            )
        return CodingWorkflowRepairResult(
            run_id=record.run_id,
            status=record.status,
            generation_status=record.generation_status,
            repair_of_run_id=parent.run_id,
            review_id=record.review_id,
            next_action=record.next_action,
            files_changed=record.files_changed,
            safe_message=result.safe_message,
            events=events,
            metadata=record.metadata,
        )

    def _eligibility_error(
        self,
        run: CodingWorkflowRunRecord,
        events: tuple[CodingWorkflowEventRecord, ...],
    ) -> tuple[str, str] | None:
        if run.status != "failed":
            return REPAIR_NOT_ALLOWED, "Repair is only available for failed workflow runs."
        if not run.review_id:
            return REPAIR_NOT_ALLOWED, "Repair requires a parent review."
        build_failed = [event for event in events if event.event_type == "build.failed" and event.stage == "build"]
        if not build_failed or not run.failure_code or not run.failure_code.startswith("CODING_WORKFLOW_BUILD"):
            return REPAIR_REQUIRES_BUILD_FAILURE, "Repair requires a build-failed workflow run."
        apply_completed = next((event.sequence for event in events if event.event_type == "apply.completed"), None)
        build_failed_sequence = build_failed[-1].sequence
        if apply_completed is None or apply_completed >= build_failed_sequence:
            return REPAIR_REQUIRES_BUILD_FAILURE, "Repair requires apply to complete before the failed build."
        return None

    def _repair_attempt_count(self, parent_run_id: str) -> int:
        count = 0
        for run in self._store.list_runs(limit=1000):
            if run.metadata.get("repair_of_run_id") == parent_run_id and run.metadata.get("repair_reason") == "build_failed":
                count += 1
        return count


def _persist_repair_started(
    store: CodingWorkflowStore,
    repair_run_id: str,
    *,
    parent: CodingWorkflowRunRecord,
    attempt: int,
    metadata: Mapping[str, object],
) -> None:
    now = _utc_now()
    record = CodingWorkflowRunRecord(
        run_id=repair_run_id,
        task_id=parent.task_id,
        project_id=parent.project_id,
        provider_id=REAL_API_CODING_AGENT_PROVIDER_ID,
        provider_type="api_coding_agent",
        status="repairing",
        generation_status="running",
        next_action=None,
        created_at=now,
        updated_at=now,
        safe_summary="Build-failure repair review generation is in progress.",
        safe_message="Repair review generation is in progress.",
        metadata=metadata,
    )
    store.persist_run(record, (
        _event(
            record.run_id,
            1,
            "repair.started",
            "repair",
            "repairing",
            "Build-failure repair review generation started.",
            metadata={
                "repair_of_run_id": parent.run_id,
                "repair_attempt_number": attempt,
                "repair_reason": "build_failed",
            },
        ),
    ))


def _persist_repair_result(
    store: CodingWorkflowStore,
    result: CodingProviderRunResult,
    *,
    parent: CodingWorkflowRunRecord,
    attempt: int,
    metadata: Mapping[str, object],
) -> tuple[CodingWorkflowRunRecord, tuple[CodingWorkflowEventRecord, ...]]:
    awaiting_apply = result.status.value == "awaiting_apply"
    generation_status = "repair_review_created" if awaiting_apply else "failed"
    failure_code = result.failure_code.value if result.failure_code else (None if awaiting_apply else "CODING_WORKFLOW_REPAIR_FAILED")
    record = store.transition_run(
        result.events[0].run_id if result.events else f"coding-workflow-repair-{uuid.uuid4().hex}",
        expected_statuses=("repairing",),
        status=result.status.value,
        generation_status=generation_status,
        review_id=result.review_id,
        next_action="await_user_approval" if awaiting_apply else None,
        files_changed=result.files_changed,
        safe_summary=result.summary or None,
        failure_code=failure_code,
        safe_message=result.safe_message or ("Repair review created. Explicit approval is required before apply." if awaiting_apply else "Repair review generation failed safely."),
        metadata=metadata,
    )
    events: list[CodingWorkflowEventRecord] = list(store.list_events(record.run_id))
    for event in result.events:
        sequence = len(events) + 1
        appended = _event(
            record.run_id,
            sequence,
            event.event_type,
            event.stage.value,
            event.status.value,
            event.safe_message,
            metadata=event.metadata,
            created_at=event.timestamp,
        )
        store.append_event(appended)
        events.append(appended)
    if awaiting_apply:
        appended = _event(
            record.run_id,
            len(events) + 1,
            "apply.waiting_for_approval",
            "review",
            "awaiting_apply",
            "Repair review created. Explicit approval is required before apply.",
            metadata={"provider_id": REAL_API_CODING_AGENT_PROVIDER_ID, "real_api_calls": True},
        )
        store.append_event(appended)
        events.append(appended)
    return record, tuple(events)


def _event(
    run_id: str,
    sequence: int,
    event_type: str,
    stage: str,
    status: str,
    message: str,
    *,
    metadata: Mapping[str, object] | None = None,
    created_at: str | None = None,
) -> CodingWorkflowEventRecord:
    digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
    return CodingWorkflowEventRecord(
        event_id=f"event-{digest}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        stage=stage,
        status=status,
        safe_message=message,
        created_at=created_at or _utc_now(),
        metadata=dict(metadata or {}),
    )


def _repair_prompt(
    parent: CodingWorkflowRunRecord,
    events: tuple[CodingWorkflowEventRecord, ...],
    attempt: int,
) -> str:
    return "\n".join([
        "Generate a review-only ForgeX coding repair proposal for a build failure.",
        "Return only strict JSON matching schema_version forgex.api_coding_agent.v1.",
        "Do not include markdown, prose, or command execution.",
        "Do not assume active workspace mutation; ForgeX will create a review and wait for approval.",
        "",
        f"Repair attempt: {attempt}",
        f"Parent run status: {parent.status}",
        f"Parent review ID: {parent.review_id or 'unknown'}",
        f"Parent safe summary: {_sanitize_text(parent.safe_summary or '')}",
        f"Previously changed files: {', '.join(parent.files_changed) if parent.files_changed else 'none'}",
        "",
        "Bounded build error summary:",
        _build_error_summary(parent, events),
    ])


def _build_error_summary(
    parent: CodingWorkflowRunRecord,
    events: tuple[CodingWorkflowEventRecord, ...],
) -> str:
    parts = [
        f"failure_code: {parent.failure_code or 'unknown'}",
        f"safe_message: {parent.safe_message or 'Build failed safely.'}",
    ]
    for event in events:
        if event.event_type == "build.failed":
            parts.append(f"event_message: {event.safe_message}")
            for key in ("failure_code", "build_status", "environment", "board"):
                value = event.metadata.get(key)
                if isinstance(value, (str, int, float, bool)):
                    parts.append(f"{key}: {value}")
    for key in ("build_status", "build_environment", "build_board", "build_duration_ms", "build_warnings_count"):
        value = parent.metadata.get(key)
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}: {value}")
    text = _sanitize_text("\n".join(parts))
    lines = text.splitlines()[:MAX_ERROR_LINES]
    bounded = "\n".join(lines)
    if len(bounded) > MAX_BUILD_ERROR_CHARS:
        bounded = bounded[:MAX_BUILD_ERROR_CHARS]
    return bounded


def _sanitize_text(value: str) -> str:
    text = "".join(ch if ch in "\n\t" or ord(ch) >= 32 else " " for ch in value)
    text = _WINDOWS_ABS_RE.sub("[REDACTED_PATH]", text)
    text = _UNC_RE.sub("[REDACTED_PATH]", text)
    text = _POSIX_PRIVATE_RE.sub("[REDACTED_PATH]", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _repair_selected_files(parent: CodingWorkflowRunRecord, selected_files: list[str] | None) -> list[str]:
    values = selected_files if selected_files else ["platformio.ini", *parent.files_changed]
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip().replace("\\", "/")
        if not text or text.casefold() in seen:
            continue
        cleaned.append(text)
        seen.add(text.casefold())
    return cleaned[:20]


def _generation_metadata(value: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "context_mode",
        "context_file_count",
        "context_total_bytes",
        "context_truncated",
        "context_excluded_count",
        "model_provider_id",
        "model_id",
        "model_output_chars",
        "provider_id",
        "real_api_calls",
        "command_suggestion_count",
        "risk_count",
        "next_step_count",
        "validation_error_count",
    }
    return {key: item for key, item in value.items() if key in allowed}


def _rejected(run_id: str, status: str, code: str, message: str) -> CodingWorkflowRepairResult:
    return CodingWorkflowRepairResult(run_id=run_id, status=status, failure_code=code, safe_message=message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CODING_AGENT_REPAIR_LOOP_FLAG",
    "CodingWorkflowRepairResult",
    "CodingWorkflowRepairService",
]
