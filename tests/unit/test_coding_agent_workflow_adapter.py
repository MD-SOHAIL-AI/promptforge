from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_provider_contracts import (
    CodingContextMode,
    CodingProviderFailureCode,
    CodingRunStatus,
)
from backend.agent_runtime.coding_provider_registry import default_coding_provider_registry
from backend.agent_runtime.coding_workflow_store import CodingWorkflowStore
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.workflow.adapters.coding_agent_adapter import (
    CodingAgentGenerationAdapter,
    CodingAgentWorkflowGenerationStatus,
)


ENABLED_ENV = {
    "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1",
    "FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1",
}


def make_service(tmp_path: Path, *, enabled: bool) -> tuple[ApiCodingAgentService, BridgeDiffService, Path]:
    managed = tmp_path / "forgex-state" / "api-coding-agent-sandboxes"
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=tmp_path / "forgex-state" / "snapshots.jsonl",
        reviews_path=tmp_path / "forgex-state" / "reviews.jsonl",
    ))
    return ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(managed),
        review_service=reviews,
        enabled=enabled,
    ), reviews, managed


def make_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("active workspace\n", encoding="utf-8")
    return active


def make_store(tmp_path: Path) -> CodingWorkflowStore:
    return CodingWorkflowStore.from_state_directory(tmp_path / "forgex-state")


def test_workflow_flag_disabled_refuses_before_registry_or_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = make_workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)

    def forbidden_call(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider must not run while workflow flag is disabled")

    monkeypatch.setattr(service, "generate_review_from_fake_provider", forbidden_call)
    result = CodingAgentGenerationAdapter(
        fake_service=service,
        store=make_store(tmp_path),
        env={"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"},
    ).generate_review("basic esp32 blink", active)

    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is CodingProviderFailureCode.UNIFIED_CODING_WORKFLOW_DISABLED
    assert reviews.list_reviews() == ()
    assert not managed.exists()


def test_fake_provider_flag_disabled_refuses_execution(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=False)
    result = CodingAgentGenerationAdapter(
        fake_service=service,
        store=make_store(tmp_path),
        env={"FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1"},
    ).generate_review("basic esp32 blink", active)

    assert result.failure_code is CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
    assert reviews.list_reviews() == ()
    assert not managed.exists()


def test_fake_flag_cannot_be_bypassed_by_injected_enabled_registry(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)
    enabled_registry = default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"})
    result = CodingAgentGenerationAdapter(
        fake_service=service,
        store=make_store(tmp_path),
        env={"FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1"},
        registry=enabled_registry,
    ).generate_review("basic esp32 blink", active)
    assert result.failure_code is CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
    assert reviews.list_reviews() == ()
    assert not managed.exists()


@pytest.mark.parametrize("context_mode", [CodingContextMode.SELECTED_FILES, CodingContextMode.FILE_TREE_ONLY])
def test_both_flags_enable_fake_review_and_pause_at_apply_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    context_mode: CodingContextMode,
) -> None:
    active = make_workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)

    def forbidden_process(*args: object, **kwargs: object) -> object:
        raise AssertionError("workflow adapter must not run commands, build, flash, or monitor")

    monkeypatch.setattr(subprocess, "run", forbidden_process)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    store = make_store(tmp_path)
    result = CodingAgentGenerationAdapter(fake_service=service, store=store, env=ENABLED_ENV).generate_review(
        "basic esp32 blink",
        active,
        context_mode=context_mode,
    )

    assert result.status is CodingRunStatus.AWAITING_APPLY
    assert result.generation_status is CodingAgentWorkflowGenerationStatus.REVIEW_CREATED
    assert result.review_created is True
    assert result.awaiting_apply is True
    assert result.review_id is not None
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert [event.event_type for event in result.events] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]
    assert result.downstream_stages_started is False
    assert result.to_safe_dict()["next_action"] == "await_user_approval"
    assert "commands_suggested" not in result.to_safe_dict()
    assert not hasattr(result, "generated_project")
    assert not hasattr(result, "build_result")
    assert not hasattr(result, "flash_result")
    assert not hasattr(result, "monitor_result")

    assert (active / "README.md").read_text(encoding="utf-8") == "active workspace\n"
    assert not (active / "platformio.ini").exists()
    assert not (active / "src" / "main.cpp").exists()
    review = reviews.get_review(result.review_id)
    sandbox = Path(review.workspace_root)
    sandbox.resolve().relative_to(managed.resolve())
    assert (sandbox / "platformio.ini").is_file()
    assert (sandbox / "src" / "main.cpp").is_file()
    persisted = store.get_run(result.run_id)
    assert persisted.status == "awaiting_apply"
    assert persisted.generation_status == "review_created"
    assert persisted.review_id == result.review_id
    assert persisted.files_changed == ("platformio.ini", "src/main.cpp")
    assert persisted.next_action == "await_user_approval"
    assert [event.event_type for event in store.list_events(result.run_id)] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]


@pytest.mark.parametrize(
    ("prompt", "failure_code"),
    [
        ("unsafe path", CodingProviderFailureCode.UNSAFE_OUTPUT),
        ("protected path", CodingProviderFailureCode.UNSAFE_OUTPUT),
        ("invalid json", CodingProviderFailureCode.API_CONTRACT_INVALID),
        ("oversized", CodingProviderFailureCode.OVERSIZED_OUTPUT),
    ],
)
def test_invalid_fake_proposals_fail_before_apply_gate(
    tmp_path: Path,
    prompt: str,
    failure_code: CodingProviderFailureCode,
) -> None:
    active = make_workspace(tmp_path)
    service, reviews, _ = make_service(tmp_path, enabled=True)
    store = make_store(tmp_path)
    result = CodingAgentGenerationAdapter(fake_service=service, store=store, env=ENABLED_ENV).generate_review(prompt, active)

    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is failure_code
    assert result.review_id is None
    assert result.downstream_stages_started is False
    assert result.events[-1].event_type == "generation.failed"
    assert reviews.list_reviews() == ()
    assert (active / "README.md").read_text(encoding="utf-8") == "active workspace\n"
    persisted = store.get_run(result.run_id)
    assert persisted.status == "failed"
    assert persisted.failure_code == failure_code.value
    assert persisted.review_id is None
    assert store.list_events(result.run_id)[-1].event_type == "generation.failed"


def test_unsupported_explicit_provider_fails_without_execution(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)
    result = CodingAgentGenerationAdapter(fake_service=service, store=make_store(tmp_path), env=ENABLED_ENV).generate_review(
        "basic esp32 blink",
        active,
        explicit_provider_id="codex_cli",
    )
    assert result.failure_code is CodingProviderFailureCode.PROVIDER_NOT_IMPLEMENTED
    assert reviews.list_reviews() == ()
    assert not managed.exists()
