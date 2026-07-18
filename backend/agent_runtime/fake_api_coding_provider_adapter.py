"""Unified contract adapter for the dev-only fake API coding agent."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .api_coding_agent_service import (
    FAKE_API_CODING_AGENT_DISABLED,
    FAKE_API_CODING_AGENT_PROVIDER_ID,
    ApiCodingAgentService,
    ApiCodingAgentServiceError,
)
from .coding_provider_contracts import (
    CodingProviderEvent,
    CodingProviderFailureCode,
    CodingProviderRunRequest,
    CodingProviderRunResult,
    CodingProviderSelectionRequest,
    CodingRunStage,
    CodingRunStatus,
)
from .coding_provider_registry import CodingProviderRegistry, CodingProviderRegistryError


class FakeApiCodingProviderAdapter:
    """Converts the existing fake review service into the unified run result."""

    provider_id = FAKE_API_CODING_AGENT_PROVIDER_ID

    def __init__(self, service: ApiCodingAgentService) -> None:
        if not isinstance(service, ApiCodingAgentService):
            raise TypeError("service must be an ApiCodingAgentService")
        self.service = service

    def run(self, request: CodingProviderRunRequest, workspace_path: Path) -> CodingProviderRunResult:
        if not isinstance(request, CodingProviderRunRequest):
            raise TypeError("request must be a CodingProviderRunRequest")
        if request.provider_id != self.provider_id:
            return _failure_result(
                request.run_id,
                CodingProviderFailureCode.PROVIDER_NOT_IMPLEMENTED,
                "The selected coding provider is not implemented by the fake adapter.",
            )

        initial_events = (
            _event(request.run_id, 1, "provider.selected", CodingRunStage.SELECTION, CodingRunStatus.VALIDATING, "Fake API coding provider selected."),
            _event(request.run_id, 2, "generation.started", CodingRunStage.GENERATION, CodingRunStatus.RUNNING, "Fake API coding generation started."),
        )
        try:
            service_result = self.service.generate_review_from_fake_provider(request.prompt, workspace_path)
        except ApiCodingAgentServiceError as exc:
            code, message = _map_service_error(exc)
            return _failure_result(request.run_id, code, message, initial_events=initial_events)
        except Exception:
            return _failure_result(
                request.run_id,
                CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
                "Fake API coding provider failed safely before review handoff.",
                initial_events=initial_events,
            )

        if service_result.status != "review_created" or not service_result.review_id:
            code = _map_validation_errors(service_result.validation_errors)
            return _failure_result(
                request.run_id,
                code,
                _validation_message(code),
                initial_events=initial_events,
                metadata={"validation_error_count": len(service_result.validation_errors)},
            )

        try:
            review = self.service.review_service.get_review(service_result.review_id)
            files_changed = tuple(item.path for item in review.changed_files if item.safe)
            if len(files_changed) != len(review.changed_files):
                return _failure_result(
                    request.run_id,
                    CodingProviderFailureCode.UNSAFE_OUTPUT,
                    "ForgeX review contained an unsafe changed path.",
                    initial_events=initial_events,
                )
        except Exception:
            return _failure_result(
                request.run_id,
                CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
                "ForgeX could not load the generated review safely.",
                initial_events=initial_events,
            )

        events = initial_events + (
            _event(request.run_id, 3, "generation.completed", CodingRunStage.GENERATION, CodingRunStatus.AWAITING_REVIEW, "Fake API coding generation completed."),
            _event(request.run_id, 4, "review.created", CodingRunStage.REVIEW, CodingRunStatus.AWAITING_APPLY, "ForgeX review created; user approval is required before apply."),
        )
        return CodingProviderRunResult(
            status=CodingRunStatus.AWAITING_APPLY,
            summary=service_result.summary,
            review_id=review.review_id,
            files_changed=files_changed,
            events=events,
            safe_message="Fake API coding proposal was validated and converted into a ForgeX review.",
            metadata={
                "provider_id": self.provider_id,
                "dev_only": True,
                "real_api_calls": False,
                "command_suggestion_count": len(service_result.commands_suggested),
                "risk_count": len(service_result.risks),
                "next_step_count": len(service_result.next_steps),
            },
        )


def run_coding_provider(
    registry: CodingProviderRegistry,
    request: CodingProviderRunRequest,
    workspace_path: Path,
    *,
    fake_adapter: FakeApiCodingProviderAdapter | None = None,
) -> CodingProviderRunResult:
    """Execute the one Phase 3 supported provider without enabling live routing."""

    if not isinstance(registry, CodingProviderRegistry):
        raise TypeError("registry must be a CodingProviderRegistry")
    if not isinstance(request, CodingProviderRunRequest):
        raise TypeError("request must be a CodingProviderRunRequest")
    if request.provider_id != FAKE_API_CODING_AGENT_PROVIDER_ID:
        return _failure_result(
            request.run_id,
            CodingProviderFailureCode.PROVIDER_NOT_IMPLEMENTED,
            "This coding provider has no Phase 3 execution adapter.",
            provider_id=request.provider_id,
        )
    try:
        registry.get(request.provider_id)
    except CodingProviderRegistryError:
        return _failure_result(
            request.run_id,
            CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
            "Fake API coding provider is not registered.",
        )

    selection = registry.select(CodingProviderSelectionRequest(
        prompt=request.prompt,
        explicit_provider_id=request.provider_id,
        required_capabilities=request.requested_capabilities,
        context_mode=request.context_mode,
        allow_dev_providers=True,
    ))
    if not selection.selected:
        code = selection.failure_code or CodingProviderFailureCode.PROVIDER_UNAVAILABLE
        if code is CodingProviderFailureCode.PROVIDER_DISABLED:
            code = CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
        return _failure_result(request.run_id, code, selection.safe_message)
    if fake_adapter is None:
        return _failure_result(
            request.run_id,
            CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
            "Fake API coding provider adapter is unavailable.",
        )
    return fake_adapter.run(request, workspace_path)


def _map_validation_errors(errors: tuple[str, ...]) -> CodingProviderFailureCode:
    text = " ".join(errors).casefold()
    if any(marker in text for marker in ("max_file_bytes", "max_total_bytes", "max_files", "exceeds limit", "too large")):
        return CodingProviderFailureCode.OVERSIZED_OUTPUT
    if any(marker in text for marker in (".path", "binary", "sensitive", "protected", "delete is disabled", "duplicates another file path")):
        return CodingProviderFailureCode.UNSAFE_OUTPUT
    return CodingProviderFailureCode.API_CONTRACT_INVALID


def _map_service_error(exc: ApiCodingAgentServiceError) -> tuple[CodingProviderFailureCode, str]:
    if exc.code == FAKE_API_CODING_AGENT_DISABLED:
        return CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED, "Fake API coding agent is disabled."
    if exc.code in {
        "API_CODING_AGENT_PATH_UNSAFE",
        "API_CODING_AGENT_ACTIVE_WORKSPACE_CHANGED",
        "API_CODING_AGENT_DIFF_INVALID",
        "API_CODING_AGENT_DELETE_INVALID",
        "API_CODING_AGENT_CONTENT_INVALID",
        "FAKE_API_CODING_AGENT_PATH_UNSAFE",
        "FAKE_API_CODING_AGENT_ACTIVE_WORKSPACE_CHANGED",
        "FAKE_API_CODING_AGENT_DIFF_INVALID",
        "FAKE_API_CODING_AGENT_DELETE_INVALID",
        "FAKE_API_CODING_AGENT_CONTENT_INVALID",
    }:
        return CodingProviderFailureCode.UNSAFE_OUTPUT, "Fake API coding proposal failed ForgeX safety validation."
    if exc.code in {"FAKE_API_CODING_AGENT_PROMPT_INVALID", "FAKE_API_CODING_AGENT_WORKSPACE_INVALID", "API_CODING_AGENT_WORKSPACE_INVALID"}:
        return CodingProviderFailureCode.INVALID_REQUEST, "Fake API coding request was invalid."
    return CodingProviderFailureCode.PROVIDER_UNAVAILABLE, "Fake API coding provider was unavailable."


def _validation_message(code: CodingProviderFailureCode) -> str:
    if code is CodingProviderFailureCode.OVERSIZED_OUTPUT:
        return "Fake API coding proposal exceeded ForgeX size limits."
    if code is CodingProviderFailureCode.UNSAFE_OUTPUT:
        return "Fake API coding proposal failed ForgeX safety validation."
    return "Fake API coding proposal did not satisfy the structured contract."


def _failure_result(
    run_id: str,
    code: CodingProviderFailureCode,
    message: str,
    *,
    initial_events: tuple[CodingProviderEvent, ...] = (),
    metadata: dict[str, object] | None = None,
    provider_id: str = FAKE_API_CODING_AGENT_PROVIDER_ID,
) -> CodingProviderRunResult:
    sequence = initial_events[-1].sequence + 1 if initial_events else 1
    events = initial_events + (
        _event(
            run_id,
            sequence,
            "generation.failed",
            CodingRunStage.GENERATION,
            CodingRunStatus.FAILED,
            message,
            provider_id=provider_id,
        ),
    )
    return CodingProviderRunResult(
        status=CodingRunStatus.FAILED,
        summary="",
        events=events,
        failure_code=code,
        safe_message=message,
        metadata={} if metadata is None else metadata,
    )


def _event(
    run_id: str,
    sequence: int,
    event_type: str,
    stage: CodingRunStage,
    status: CodingRunStatus,
    message: str,
    *,
    provider_id: str = FAKE_API_CODING_AGENT_PROVIDER_ID,
) -> CodingProviderEvent:
    digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
    return CodingProviderEvent(
        event_id=f"event-{digest}",
        sequence=sequence,
        run_id=run_id,
        event_type=event_type,
        stage=stage,
        status=status,
        safe_message=message,
        metadata={"provider_id": provider_id, "dev_only": provider_id == FAKE_API_CODING_AGENT_PROVIDER_ID},
    )
