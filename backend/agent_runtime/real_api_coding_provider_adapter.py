"""Review-only real API coding-provider adapter through model_router."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Protocol

from ..model_router.models import ModelRequest
from ..services.llm_service import LLMConfigurationError, LLMError
from .api_agent_contracts import ApiCodingAgentContractError, parse_api_coding_agent_response
from .api_coding_agent_service import ApiCodingAgentService, ApiCodingAgentServiceError
from .api_coding_context import ApiCodingContext, build_api_coding_context
from .coding_provider_contracts import (
    CodingProviderEvent,
    CodingProviderFailureCode,
    CodingProviderRunResult,
    CodingRunStage,
    CodingRunStatus,
)


REAL_API_CODING_AGENT_FLAG = "FORGEX_ENABLE_REAL_API_CODING_AGENT"
REAL_API_CODING_AGENT_PROVIDER_ID = "api_coding_agent"


class ModelRouterLike(Protocol):
    async def generate_model(self, request: ModelRequest) -> object: ...


class RealApiCodingProviderAdapter:
    """Generate a validated coding proposal through model_router and create a review."""

    provider_id = REAL_API_CODING_AGENT_PROVIDER_ID

    def __init__(
        self,
        *,
        model_router: ModelRouterLike,
        review_service: ApiCodingAgentService,
        env: dict[str, str] | None = None,
    ) -> None:
        self.model_router = model_router
        self.review_service = review_service
        source = os.environ if env is None else env
        self.enabled = source.get(REAL_API_CODING_AGENT_FLAG, "").strip() == "1"

    async def generate_review(
        self,
        *,
        prompt: str,
        workspace_path: Path,
        context_mode: str = "selected_files",
        selected_files: list[str] | None = None,
        model_route: str | None = None,
        provider_id: str | None = None,
        run_id: str | None = None,
    ) -> CodingProviderRunResult:
        run_id = run_id or f"coding-workflow-{uuid.uuid4().hex}"
        initial_events = (
            _event(run_id, 1, "provider.selected", CodingRunStage.SELECTION, CodingRunStatus.VALIDATING, "API coding provider selected."),
            _event(run_id, 2, "generation.started", CodingRunStage.GENERATION, CodingRunStatus.RUNNING, "API coding generation started."),
        )
        if not self.enabled:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.REAL_API_CODING_AGENT_DISABLED,
                "Real API coding agent is disabled.",
                initial_events=initial_events,
            )
        if not isinstance(prompt, str) or not prompt.strip():
            return _failure_result(
                run_id,
                CodingProviderFailureCode.INVALID_REQUEST,
                "Prompt must be a non-empty string.",
                initial_events=initial_events,
            )

        try:
            context = build_api_coding_context(
                workspace_path,
                prompt=prompt,
                context_mode=context_mode,
                selected_files=selected_files,
            )
        except Exception:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.API_CODING_CONTEXT_INVALID,
                "API coding context could not be built safely.",
                initial_events=initial_events,
            )
        if not context.files and not context.tree_summary:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.API_CODING_CONTEXT_EMPTY,
                "API coding context did not include any safe project context.",
                initial_events=initial_events,
                metadata=_context_metadata(context),
            )

        unavailable = _availability_failure(self.model_router, provider_id)
        if unavailable is not None:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.REAL_API_CODING_AGENT_UNAVAILABLE,
                unavailable,
                initial_events=initial_events,
                metadata=_context_metadata(context),
            )

        try:
            response = await self.model_router.generate_model(ModelRequest(
                prompt=_model_prompt(prompt, context),
                system_prompt=_system_prompt(),
                task_type="code_generation",
                provider_id=provider_id,
                model_id=model_route,
                temperature=0.1,
                max_tokens=4096,
                allow_fallback=False,
                local_only=False,
                metadata={
                    "caller": "real_api_coding_agent",
                    "context_mode": context.context_mode,
                    "context_file_count": len(context.files),
                    "context_total_bytes": context.total_bytes,
                    "context_truncated": context.truncated,
                },
            ))
        except LLMConfigurationError:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.REAL_API_CODING_AGENT_UNAVAILABLE,
                "Configured model router provider is unavailable for API coding generation.",
                initial_events=initial_events,
                metadata=_context_metadata(context),
            )
        except LLMError:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.API_CODING_MODEL_CALL_FAILED,
                "API coding model call failed safely.",
                initial_events=initial_events,
                metadata=_context_metadata(context),
            )
        except Exception:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.API_CODING_MODEL_CALL_FAILED,
                "API coding model call failed safely.",
                initial_events=initial_events,
                metadata=_context_metadata(context),
            )

        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            return _failure_result(
                run_id,
                CodingProviderFailureCode.API_CODING_MODEL_OUTPUT_INVALID,
                "API coding model returned empty or invalid output.",
                initial_events=initial_events,
                metadata=_response_metadata(response, context),
            )
        try:
            proposal = parse_api_coding_agent_response(content)
        except ApiCodingAgentContractError as exc:
            code = _map_contract_errors(exc.errors)
            return _failure_result(
                run_id,
                code,
                _contract_message(code),
                initial_events=initial_events,
                metadata={
                    **_response_metadata(response, context),
                    "validation_error_count": len(exc.errors),
                },
            )

        try:
            service_result = self.review_service.create_review_from_validated_proposal(
                proposal,
                workspace_path,
                run_id=run_id,
                provider_id=self.provider_id,
                artifact_source="api_coding_agent_real",
                artifact_type="api_coding_agent_workspace_diff",
                artifact_metadata={
                    "schema_version": proposal.schema_version,
                    "real_api_calls": True,
                    **_response_metadata(response, context),
                },
            )
        except ApiCodingAgentServiceError as exc:
            code, message = _map_review_error(exc)
            return _failure_result(
                run_id,
                code,
                message,
                initial_events=initial_events,
                metadata=_response_metadata(response, context),
            )
        except Exception:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
                "ForgeX could not create a review from the API coding proposal safely.",
                initial_events=initial_events,
                metadata=_response_metadata(response, context),
            )

        if service_result.status != "review_created" or not service_result.review_id:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
                "API coding proposal did not create a ForgeX review.",
                initial_events=initial_events,
                metadata=_response_metadata(response, context),
            )

        try:
            review = self.review_service.review_service.get_review(service_result.review_id)
            files_changed = tuple(item.path for item in review.changed_files if item.safe)
            if len(files_changed) != len(review.changed_files):
                return _failure_result(
                    run_id,
                    CodingProviderFailureCode.UNSAFE_OUTPUT,
                    "ForgeX review contained an unsafe changed path.",
                    initial_events=initial_events,
                    metadata=_response_metadata(response, context),
                )
        except Exception:
            return _failure_result(
                run_id,
                CodingProviderFailureCode.PROVIDER_UNAVAILABLE,
                "ForgeX could not load the generated review safely.",
                initial_events=initial_events,
                metadata=_response_metadata(response, context),
            )

        events = initial_events + (
            _event(run_id, 3, "generation.completed", CodingRunStage.GENERATION, CodingRunStatus.AWAITING_REVIEW, "API coding generation completed."),
            _event(run_id, 4, "review.created", CodingRunStage.REVIEW, CodingRunStatus.AWAITING_APPLY, "ForgeX review created; user approval is required before apply."),
        )
        return CodingProviderRunResult(
            status=CodingRunStatus.AWAITING_APPLY,
            summary=proposal.summary,
            review_id=review.review_id,
            files_changed=files_changed,
            events=events,
            safe_message="API coding proposal was validated and converted into a ForgeX review.",
            metadata={
                **_response_metadata(response, context),
                "provider_id": self.provider_id,
                "real_api_calls": True,
                "command_suggestion_count": len(proposal.commands_suggested),
                "risk_count": len(proposal.risks),
                "next_step_count": len(proposal.next_steps),
            },
        )


def _system_prompt() -> str:
    return (
        "You are ForgeX's review-only coding proposal generator. "
        "Return ONLY valid JSON. Do not use markdown fences. "
        "Do not include prose before or after JSON. "
        "Use schema_version = forgex.api_coding_agent.v1. "
        "Use relative paths only. Do not include secrets. "
        "Do not include command execution. Commands may be listed only as advisory commands_suggested. "
        "Do not modify .env, .git, generated folders, or protected paths."
    )


def _model_prompt(prompt: str, context: ApiCodingContext) -> str:
    payload = context.to_dict()
    return "\n".join([
        "Create a ForgeX coding proposal for the user's request.",
        "Return ONLY valid JSON.",
        "Do not use markdown fences.",
        "Do not include prose before or after JSON.",
        "Use schema_version = forgex.api_coding_agent.v1.",
        "The JSON object must contain fields: schema_version, summary, files, commands_suggested, risks, next_steps.",
        "Use relative paths only.",
        "Do not include secrets.",
        "Do not include command execution.",
        "Do not modify .env, .git, generated folders, or protected paths.",
        "Allowed file action is create_or_update. Delete operations are disabled.",
        "commands_suggested are advisory only and will not be executed.",
        "",
        "User request:",
        prompt,
        "",
        "Bounded safe workspace context:",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    ])


def _availability_failure(model_router: object, provider_id: str | None) -> str | None:
    registry = getattr(model_router, "registry", None)
    if registry is None:
        return None
    try:
        target = provider_id or registry.route_for_task("code_generation").provider_id
        provider = registry.provider(target)
    except Exception:
        return "Model router provider is unavailable for API coding generation."
    connections = getattr(registry, "connections", None)
    if connections is None:
        return "Canonical provider connection status is unavailable."
    connection = connections.refresh_status(f"{target}.default")
    if not connection.connection_ready:
        return "Canonical provider authentication or connection health is not ready."
    if getattr(provider, "provider_type", None) != "api_provider" or getattr(provider, "auth_type", None) != "api_key":
        return "Selected model router provider is not an API provider."
    if bool(getattr(provider, "local", False)):
        return "Selected model router provider is local-only, not a real API provider."
    if not bool(getattr(provider, "configured", False)) or not bool(getattr(provider, "enabled", False)):
        return "Model router provider is not configured for API coding generation."
    if str(getattr(provider, "health_status", "unknown")).casefold() in {
        "authentication_required",
        "error",
        "not_configured",
        "offline",
        "unavailable",
    }:
        return "Model router provider health is not ready for API coding generation."
    return None


def _context_metadata(context: ApiCodingContext) -> dict[str, object]:
    return {
        "context_mode": context.context_mode,
        "context_file_count": len(context.files),
        "context_total_bytes": context.total_bytes,
        "context_truncated": context.truncated,
        "context_excluded_count": len(context.excluded_files),
    }


def _response_metadata(response: object, context: ApiCodingContext) -> dict[str, object]:
    return {
        **_context_metadata(context),
        "model_provider_id": str(getattr(response, "provider_id", ""))[:128],
        "model_id": str(getattr(response, "model_id", ""))[:256],
        "model_output_chars": len(str(getattr(response, "content", ""))),
    }


def _map_contract_errors(errors: tuple[str, ...]) -> CodingProviderFailureCode:
    text = " ".join(errors).casefold()
    if any(marker in text for marker in ("max_file_bytes", "max_total_bytes", "max_files", "exceeds limit", "too large")):
        return CodingProviderFailureCode.OVERSIZED_OUTPUT
    if any(marker in text for marker in (".path", "binary", "sensitive", "protected", "delete is disabled", "duplicates another file path")):
        return CodingProviderFailureCode.UNSAFE_OUTPUT
    return CodingProviderFailureCode.API_CONTRACT_INVALID


def _contract_message(code: CodingProviderFailureCode) -> str:
    if code is CodingProviderFailureCode.OVERSIZED_OUTPUT:
        return "API coding model proposal exceeded ForgeX size limits."
    if code is CodingProviderFailureCode.UNSAFE_OUTPUT:
        return "API coding model proposal failed ForgeX safety validation."
    return "API coding model output did not satisfy the structured contract."


def _map_review_error(exc: ApiCodingAgentServiceError) -> tuple[CodingProviderFailureCode, str]:
    if exc.code in {
        "FAKE_API_CODING_AGENT_PATH_UNSAFE",
        "FAKE_API_CODING_AGENT_ACTIVE_WORKSPACE_CHANGED",
        "FAKE_API_CODING_AGENT_DIFF_INVALID",
        "FAKE_API_CODING_AGENT_DELETE_INVALID",
        "FAKE_API_CODING_AGENT_CONTENT_INVALID",
        "API_CODING_AGENT_PATH_UNSAFE",
        "API_CODING_AGENT_ACTIVE_WORKSPACE_CHANGED",
        "API_CODING_AGENT_DIFF_INVALID",
        "API_CODING_AGENT_DELETE_INVALID",
        "API_CODING_AGENT_CONTENT_INVALID",
    }:
        return CodingProviderFailureCode.UNSAFE_OUTPUT, "API coding proposal failed ForgeX review safety validation."
    if exc.code == "API_CODING_AGENT_WORKSPACE_INVALID":
        return CodingProviderFailureCode.INVALID_REQUEST, "API coding workspace was invalid."
    return CodingProviderFailureCode.PROVIDER_UNAVAILABLE, "API coding review creation was unavailable."


def _failure_result(
    run_id: str,
    code: CodingProviderFailureCode,
    message: str,
    *,
    initial_events: tuple[CodingProviderEvent, ...] = (),
    metadata: dict[str, object] | None = None,
) -> CodingProviderRunResult:
    sequence = initial_events[-1].sequence + 1 if initial_events else 1
    events = initial_events + (
        _event(run_id, sequence, "generation.failed", CodingRunStage.GENERATION, CodingRunStatus.FAILED, message),
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
        metadata={"provider_id": REAL_API_CODING_AGENT_PROVIDER_ID, "real_api_calls": True},
    )


__all__ = [
    "REAL_API_CODING_AGENT_FLAG",
    "REAL_API_CODING_AGENT_PROVIDER_ID",
    "RealApiCodingProviderAdapter",
]
