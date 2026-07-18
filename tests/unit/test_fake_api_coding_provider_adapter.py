from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_provider_contracts import (
    CodingContextMode,
    CodingProviderCapability,
    CodingProviderFailureCode,
    CodingProviderRunRequest,
    CodingRunStatus,
)
from backend.agent_runtime.coding_provider_registry import default_coding_provider_registry
from backend.agent_runtime.fake_api_coding_provider_adapter import (
    FakeApiCodingProviderAdapter,
    run_coding_provider,
)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService


def make_service(tmp_path: Path, *, enabled: bool) -> tuple[ApiCodingAgentService, BridgeDiffService]:
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "snapshots.jsonl",
        reviews_path=tmp_path / "state" / "reviews.jsonl",
    ))
    service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(tmp_path / "managed" / "api-coding-agent-sandboxes"),
        review_service=reviews,
        enabled=enabled,
    )
    return service, reviews


def make_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("active\n", encoding="utf-8")
    return active


def make_request(prompt: str = "basic esp32 blink", *, provider_id: str = "fake_api_coding_agent") -> CodingProviderRunRequest:
    return CodingProviderRunRequest(
        run_id="coding-run-test",
        task_id="task-test",
        project_id="project-test",
        provider_id=provider_id,
        prompt=prompt,
        workspace_root_display="test workspace",
        context_mode=CodingContextMode.SELECTED_FILES,
        requested_capabilities=frozenset({
            CodingProviderCapability.GENERATE_FILES,
            CodingProviderCapability.CREATE_REVIEW,
        }),
    )


def test_registry_backed_fake_blink_creates_unified_review_result_without_active_mutation(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, reviews = make_service(tmp_path, enabled=True)
    registry = default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"})

    result = run_coding_provider(
        registry,
        make_request(),
        active,
        fake_adapter=FakeApiCodingProviderAdapter(service),
    )

    assert result.status is CodingRunStatus.AWAITING_APPLY
    assert result.review_id is not None
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert [event.event_type for event in result.events] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
    ]
    assert result.metadata["command_suggestion_count"] == 1
    assert "commands_suggested" not in result.to_safe_dict()
    assert (active / "README.md").read_text(encoding="utf-8") == "active\n"
    assert not (active / "platformio.ini").exists()
    review = reviews.get_review(result.review_id)
    assert tuple(item.path for item in review.changed_files) == result.files_changed


def test_adapter_uses_forgex_review_diff_not_service_file_claim(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)

    class ClaimingService(ApiCodingAgentService):
        def generate_review_from_fake_provider(self, prompt: str, workspace_path: Path, *, allow_delete: bool = False):
            result = super().generate_review_from_fake_provider(prompt, workspace_path, allow_delete=allow_delete)
            return replace(result, files=("untrusted-provider-claim.txt",))

    base, reviews = make_service(tmp_path, enabled=True)
    service = ClaimingService(
        sandbox_service=base.sandbox_service,
        review_service=reviews,
        enabled=True,
    )
    result = FakeApiCodingProviderAdapter(service).run(make_request(), active)
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert "untrusted-provider-claim.txt" not in result.files_changed


def test_disabled_registry_provider_returns_typed_failure_without_sandbox(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, _ = make_service(tmp_path, enabled=False)
    result = run_coding_provider(
        default_coding_provider_registry({}),
        make_request(),
        active,
        fake_adapter=FakeApiCodingProviderAdapter(service),
    )
    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
    assert [event.event_type for event in result.events] == ["generation.failed"]
    assert not service.sandbox_service.sandbox_root.exists()


def test_adapter_maps_service_disabled_failure_even_if_descriptor_is_enabled(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, _ = make_service(tmp_path, enabled=False)
    result = run_coding_provider(
        default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"}),
        make_request(),
        active,
        fake_adapter=FakeApiCodingProviderAdapter(service),
    )
    assert result.failure_code is CodingProviderFailureCode.FAKE_API_CODING_AGENT_DISABLED
    assert result.events[-1].event_type == "generation.failed"


@pytest.mark.parametrize(
    ("prompt", "failure_code"),
    [
        ("unsafe path", CodingProviderFailureCode.UNSAFE_OUTPUT),
        ("protected path", CodingProviderFailureCode.UNSAFE_OUTPUT),
        ("invalid json", CodingProviderFailureCode.API_CONTRACT_INVALID),
        ("oversized", CodingProviderFailureCode.OVERSIZED_OUTPUT),
    ],
)
def test_adapter_maps_invalid_fake_output_to_typed_failure(
    tmp_path: Path,
    prompt: str,
    failure_code: CodingProviderFailureCode,
) -> None:
    active = make_workspace(tmp_path)
    service, reviews = make_service(tmp_path, enabled=True)
    result = run_coding_provider(
        default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"}),
        make_request(prompt),
        active,
        fake_adapter=FakeApiCodingProviderAdapter(service),
    )
    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is failure_code
    assert result.events[-1].event_type == "generation.failed"
    assert reviews.list_reviews() == ()
    assert (active / "README.md").read_text(encoding="utf-8") == "active\n"


def test_non_fake_provider_returns_not_implemented_without_execution(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, _ = make_service(tmp_path, enabled=True)
    result = run_coding_provider(
        default_coding_provider_registry({"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"}),
        make_request(provider_id="codex_cli"),
        active,
        fake_adapter=FakeApiCodingProviderAdapter(service),
    )
    assert result.failure_code is CodingProviderFailureCode.PROVIDER_NOT_IMPLEMENTED
    assert result.events[-1].event_type == "generation.failed"
    assert result.events[-1].metadata["provider_id"] == "codex_cli"
    assert not service.sandbox_service.sandbox_root.exists()
