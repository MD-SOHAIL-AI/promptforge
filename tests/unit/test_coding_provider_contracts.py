from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.agent_runtime.coding_provider_contracts import (
    FORBIDDEN_PROVIDER_AUTHORITIES,
    CodingContextMode,
    CodingProviderCapability,
    CodingProviderContractError,
    CodingProviderDescriptor,
    CodingProviderEvent,
    CodingProviderExecutionMode,
    CodingProviderFailureCode,
    CodingProviderReadiness,
    CodingProviderRunRequest,
    CodingProviderRunResult,
    CodingProviderType,
    CodingRunStage,
    CodingRunStatus,
    ensure_provider_capabilities_safe,
)


def descriptor(**overrides: object) -> CodingProviderDescriptor:
    values: dict[str, object] = {
        "provider_id": "test_provider",
        "provider_type": CodingProviderType.API_CODING_AGENT,
        "display_name": "Test provider",
        "execution_mode": CodingProviderExecutionMode.STRUCTURED_PROPOSAL,
        "capabilities": frozenset({CodingProviderCapability.GENERATE_FILES}),
        "context_modes": frozenset({CodingContextMode.SELECTED_FILES}),
        "readiness": CodingProviderReadiness.READY,
        "enabled": True,
    }
    values.update(overrides)
    return CodingProviderDescriptor(**values)  # type: ignore[arg-type]


def test_valid_descriptor_is_immutable_and_safe_by_default() -> None:
    value = descriptor()
    assert value.provider_id == "test_provider"
    assert value.production_eligible is False
    assert value.can_mutate_active_workspace is False
    with pytest.raises(FrozenInstanceError):
        value.enabled = False  # type: ignore[misc]


@pytest.mark.parametrize("provider_id", ["", "Uppercase", "bad-id", "bad.id", "_leading", "a" * 65])
def test_invalid_provider_id_is_rejected(provider_id: str) -> None:
    with pytest.raises(CodingProviderContractError, match="provider_id"):
        descriptor(provider_id=provider_id)


@pytest.mark.parametrize("authority", sorted(FORBIDDEN_PROVIDER_AUTHORITIES))
def test_forbidden_authority_capabilities_are_rejected(authority: str) -> None:
    with pytest.raises(CodingProviderContractError, match="forbidden coding-provider authority"):
        descriptor(capabilities=frozenset({authority}))


def test_unknown_capability_is_rejected() -> None:
    with pytest.raises(CodingProviderContractError, match="CodingProviderCapability"):
        ensure_provider_capabilities_safe(["future_unknown_capability"])


def test_active_workspace_mutation_is_unrepresentable() -> None:
    with pytest.raises(CodingProviderContractError, match="cannot mutate the active workspace"):
        descriptor(can_mutate_active_workspace=True)


def test_dev_only_descriptor_is_represented_but_cannot_be_production_eligible() -> None:
    value = descriptor(dev_only=True)
    assert value.dev_only is True
    assert value.production_eligible is False
    with pytest.raises(CodingProviderContractError, match="dev-only"):
        descriptor(dev_only=True, production_eligible=True)


def test_disabled_or_unready_descriptor_requires_safe_reason() -> None:
    with pytest.raises(CodingProviderContractError, match="safe_failure_reason"):
        descriptor(enabled=False, readiness=CodingProviderReadiness.DISABLED)
    value = descriptor(
        enabled=True,
        readiness=CodingProviderReadiness.UNAVAILABLE,
        safe_failure_reason="Adapter is unavailable.",
    )
    assert value.readiness is CodingProviderReadiness.UNAVAILABLE


def test_run_request_freezes_metadata_and_safe_dict_omits_raw_prompt() -> None:
    request = CodingProviderRunRequest(
        run_id="run-1",
        task_id="task-1",
        project_id="project-1",
        provider_id="test_provider",
        prompt="private prompt text",
        workspace_root_display="project workspace",
        context_mode=CodingContextMode.SELECTED_FILES,
        requested_capabilities=frozenset({CodingProviderCapability.MODIFY_FILES}),
        metadata={"attempt": 1, "labels": ["test"]},
    )
    serialized = request.to_safe_dict()
    assert "prompt" not in serialized
    assert serialized["prompt_chars"] == len("private prompt text")
    assert serialized["metadata"] == {"attempt": 1, "labels": ["test"]}
    with pytest.raises(TypeError):
        request.metadata["attempt"] = 2  # type: ignore[index]


@pytest.mark.parametrize("key", ["api_key", "access_token", "password", "client_secret", "credential_ref"])
def test_run_metadata_rejects_secret_like_keys(key: str) -> None:
    with pytest.raises(CodingProviderContractError, match="unsafe key"):
        CodingProviderRunRequest(
            run_id="run-1",
            task_id="task-1",
            project_id="project-1",
            provider_id="test_provider",
            prompt="prompt",
            workspace_root_display="workspace",
            context_mode=CodingContextMode.NONE,
            metadata={key: "must-not-be-stored"},
        )


def test_run_result_models_review_handoff_without_workflow_authority() -> None:
    event = CodingProviderEvent(
        event_id="event-1",
        sequence=1,
        run_id="run-1",
        event_type="review.created",
        stage=CodingRunStage.REVIEW,
        status=CodingRunStatus.AWAITING_APPLY,
        safe_message="Review created.",
    )
    result = CodingProviderRunResult(
        status=CodingRunStatus.AWAITING_APPLY,
        summary="Two files proposed.",
        review_id="review-1",
        files_changed=("platformio.ini", "src/main.cpp"),
        events=(event,),
        safe_message="Review is awaiting user approval.",
    )
    serialized = result.to_safe_dict()
    assert serialized["review_id"] == "review-1"
    assert set(serialized) == {
        "status", "summary", "review_id", "files_changed", "events",
        "failure_code", "safe_message", "metadata",
    }
    assert not hasattr(result, "build_result")
    assert not hasattr(result, "flash_result")
    assert not hasattr(result, "monitor_result")


def test_failed_run_result_requires_typed_failure_code() -> None:
    with pytest.raises(CodingProviderContractError, match="requires failure_code"):
        CodingProviderRunResult(status=CodingRunStatus.FAILED, summary="", safe_message="Failed safely.")
    result = CodingProviderRunResult(
        status=CodingRunStatus.FAILED,
        summary="",
        failure_code=CodingProviderFailureCode.VALIDATION_FAILED,
        safe_message="Proposal validation failed.",
    )
    assert result.failure_code is CodingProviderFailureCode.VALIDATION_FAILED


@pytest.mark.parametrize("path", ["../evil.txt", "/absolute.txt", r"C:\evil.txt"])
def test_run_result_rejects_unsafe_diff_paths(path: str) -> None:
    with pytest.raises(CodingProviderContractError, match="files_changed"):
        CodingProviderRunResult(
            status=CodingRunStatus.AWAITING_REVIEW,
            summary="Unsafe diff.",
            files_changed=(path,),
            safe_message="Rejected.",
        )
