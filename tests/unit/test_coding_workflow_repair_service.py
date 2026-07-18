from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_workflow_repair_service import (
    CODING_AGENT_REPAIR_LOOP_FLAG,
    REPAIR_DISABLED,
    REPAIR_LIMIT_EXCEEDED,
    REPAIR_NOT_ALLOWED,
    REPAIR_REQUIRES_BUILD_FAILURE,
    CodingWorkflowRepairService,
)
from backend.agent_runtime.coding_workflow_store import (
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.model_router import ModelRequest, ModelResponse


ENABLED_ENV = {
    "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1",
    "FORGEX_ENABLE_REAL_API_CODING_AGENT": "1",
    CODING_AGENT_REPAIR_LOOP_FLAG: "1",
}


class StubModelRouter:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.content = content or proposal_json()
        self.exc = exc
        self.requests: list[ModelRequest] = []

    async def generate_model(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return ModelResponse(content=self.content, provider_id="openrouter", model_id="test-model")


def proposal_json(path: str = "src/main.cpp", content: str = "void setup() {}\nvoid loop() {}\n") -> str:
    return json.dumps({
        "schema_version": "forgex.api_coding_agent.v1",
        "summary": "Repair build failure.",
        "files": [{"path": path, "action": "create_or_update", "content": content}],
        "commands_suggested": [{"command": "pio run", "reason": "Advisory only"}],
        "risks": ["Review before apply."],
        "next_steps": ["Approve apply if acceptable."],
    })


def make_review_service(tmp_path: Path) -> ApiCodingAgentService:
    state = tmp_path / "state"
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=state / "snapshots.jsonl",
        reviews_path=state / "reviews.jsonl",
    ))
    return ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(state / "sandboxes"),
        review_service=reviews,
        enabled=True,
    )


def make_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "active"
    active.mkdir()
    (active / "platformio.ini").write_text("[env:esp32dev]\n", encoding="utf-8")
    (active / "src").mkdir()
    (active / "src" / "main.cpp").write_text("old source\n", encoding="utf-8")
    return active


def seed_build_failed_run(store: CodingWorkflowStore, *, run_id: str = "parent-run") -> None:
    run = CodingWorkflowRunRecord(
        run_id=run_id,
        task_id="task-parent",
        project_id="project-parent",
        provider_id="api_coding_agent",
        provider_type="api_coding_agent",
        status="failed",
        generation_status="failed",
        review_id="review-parent",
        next_action=None,
        files_changed=("src/main.cpp",),
        failure_code="CODING_WORKFLOW_BUILD_FAILED",
        safe_message="ForgeX build did not complete successfully at C:\\Users\\person\\private\\main.cpp.",
        metadata={"build_status": "failed", "build_environment": "esp32dev"},
    )
    events = (
        event(run_id, 1, "provider.selected", "selection", "validating", "provider"),
        event(run_id, 2, "generation.started", "generation", "running", "generation"),
        event(run_id, 3, "generation.completed", "generation", "awaiting_review", "generated"),
        event(run_id, 4, "review.created", "review", "awaiting_apply", "review"),
        event(run_id, 5, "apply.waiting_for_approval", "review", "awaiting_apply", "waiting"),
        event(run_id, 6, "apply.started", "apply", "applying", "apply"),
        event(run_id, 7, "apply.completed", "apply", "awaiting_build", "applied"),
        event(run_id, 8, "build.started", "build", "building", "build"),
        event(run_id, 9, "build.failed", "build", "failed", "Build failed while compiling src/main.cpp.", metadata={
            "failure_code": "CODING_WORKFLOW_BUILD_FAILED",
            "compiler_log": "raw compiler log with C:\\private\\full.log",
        }),
    )
    store.persist_run(run, events)


def seed_non_build_failed_run(store: CodingWorkflowStore) -> None:
    run = CodingWorkflowRunRecord(
        run_id="monitor-failed",
        provider_id="api_coding_agent",
        provider_type="api_coding_agent",
        status="failed",
        generation_status="failed",
        review_id="review-parent",
        files_changed=("src/main.cpp",),
        failure_code="CODING_WORKFLOW_MONITOR_FAILED",
        safe_message="Monitor failed.",
    )
    store.persist_run(run, (event("monitor-failed", 1, "monitor.failed", "monitor", "failed", "monitor failed"),))


def seed_awaiting_apply_run(store: CodingWorkflowStore) -> None:
    run = CodingWorkflowRunRecord(
        run_id="awaiting-run",
        provider_id="api_coding_agent",
        provider_type="api_coding_agent",
        status="awaiting_apply",
        generation_status="review_created",
        review_id="review-awaiting",
        next_action="await_user_approval",
        files_changed=("src/main.cpp",),
    )
    store.persist_run(run, (event("awaiting-run", 1, "review.created", "review", "awaiting_apply", "review"),))


def seed_repair_attempt(store: CodingWorkflowStore, parent_run_id: str, index: int) -> None:
    run_id = f"repair-{index}"
    run = CodingWorkflowRunRecord(
        run_id=run_id,
        provider_id="api_coding_agent",
        provider_type="api_coding_agent",
        status="failed",
        generation_status="failed",
        failure_code="API_CODING_AGENT_CONTRACT_INVALID",
        safe_message="failed",
        metadata={
            "repair_of_run_id": parent_run_id,
            "repair_reason": "build_failed",
            "repair_attempt_number": index,
        },
    )
    store.persist_run(run, (event(run_id, 1, "repair.started", "generation", "running", "repair"),))


def event(
    run_id: str,
    sequence: int,
    event_type: str,
    stage: str,
    status: str,
    message: str,
    *,
    metadata: dict[str, object] | None = None,
) -> CodingWorkflowEventRecord:
    return CodingWorkflowEventRecord(
        event_id=f"event-{run_id}-{sequence}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        stage=stage,
        status=status,
        safe_message=message,
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_repair_disabled_by_flag(tmp_path: Path) -> None:
    store = CodingWorkflowStore.from_state_directory(tmp_path / "state")
    service = CodingWorkflowRepairService(
        store=store,
        model_router=StubModelRouter(),
        review_service=make_review_service(tmp_path),
        env={**ENABLED_ENV, CODING_AGENT_REPAIR_LOOP_FLAG: "0"},
    )

    result = await service.generate_build_repair_review("missing", workspace_path=make_workspace(tmp_path))

    assert result.failure_code == REPAIR_DISABLED
    assert result.status == "rejected"


@pytest.mark.asyncio
async def test_repair_rejects_non_build_and_non_failed_states(tmp_path: Path) -> None:
    store = CodingWorkflowStore.from_state_directory(tmp_path / "state")
    seed_non_build_failed_run(store)
    seed_awaiting_apply_run(store)
    service = CodingWorkflowRepairService(
        store=store,
        model_router=StubModelRouter(),
        review_service=make_review_service(tmp_path),
        env=ENABLED_ENV,
    )
    workspace = make_workspace(tmp_path)

    non_build = await service.generate_build_repair_review("monitor-failed", workspace_path=workspace)
    awaiting = await service.generate_build_repair_review("awaiting-run", workspace_path=workspace)

    assert non_build.failure_code == REPAIR_REQUIRES_BUILD_FAILURE
    assert awaiting.failure_code == REPAIR_NOT_ALLOWED


@pytest.mark.asyncio
async def test_build_failed_run_generates_repair_review_with_bounded_context(tmp_path: Path) -> None:
    store = CodingWorkflowStore.from_state_directory(tmp_path / "state")
    seed_build_failed_run(store)
    router = StubModelRouter()
    workspace = make_workspace(tmp_path)
    service = CodingWorkflowRepairService(
        store=store,
        model_router=router,
        review_service=make_review_service(tmp_path),
        env=ENABLED_ENV,
    )

    result = await service.generate_build_repair_review("parent-run", workspace_path=workspace)

    assert result.status == "awaiting_apply"
    assert result.generation_status == "repair_review_created"
    assert result.next_action == "await_user_approval"
    assert result.repair_of_run_id == "parent-run"
    assert result.review_id
    assert result.files_changed == ("src/main.cpp",)
    assert [event.event_type for event in result.events][:3] == ["repair.started", "provider.selected", "generation.started"]
    assert "OPENAI_API_KEY" not in router.requests[0].prompt
    assert "sk-secret" not in router.requests[0].prompt
    assert "C:\\Users" not in router.requests[0].prompt
    assert "raw compiler log" not in router.requests[0].prompt
    assert (workspace / "src" / "main.cpp").read_text(encoding="utf-8") == "old source\n"

    run = store.get_run(result.run_id)
    encoded = json.dumps({"run": run.to_dict(), "events": [item.to_dict() for item in store.list_events(result.run_id)]})
    assert "old source" not in encoded
    assert "raw compiler log" not in encoded
    assert "sk-secret" not in encoded
    assert run.metadata["repair_of_run_id"] == "parent-run"
    assert run.metadata["repair_attempt_number"] == 1


@pytest.mark.asyncio
async def test_repair_attempt_limit_is_enforced(tmp_path: Path) -> None:
    store = CodingWorkflowStore.from_state_directory(tmp_path / "state")
    seed_build_failed_run(store)
    seed_repair_attempt(store, "parent-run", 1)
    seed_repair_attempt(store, "parent-run", 2)
    router = StubModelRouter()
    service = CodingWorkflowRepairService(
        store=store,
        model_router=router,
        review_service=make_review_service(tmp_path),
        env=ENABLED_ENV,
    )

    result = await service.generate_build_repair_review("parent-run", workspace_path=make_workspace(tmp_path))

    assert result.failure_code == REPAIR_LIMIT_EXCEEDED
    assert router.requests == []


@pytest.mark.asyncio
async def test_invalid_model_output_fails_safely(tmp_path: Path) -> None:
    store = CodingWorkflowStore.from_state_directory(tmp_path / "state")
    seed_build_failed_run(store)
    service = CodingWorkflowRepairService(
        store=store,
        model_router=StubModelRouter(content="not json with sk-secret"),
        review_service=make_review_service(tmp_path),
        env=ENABLED_ENV,
    )

    result = await service.generate_build_repair_review("parent-run", workspace_path=make_workspace(tmp_path))

    assert result.status == "failed"
    assert result.failure_code == "API_CODING_AGENT_CONTRACT_INVALID"
    assert "sk-secret" not in json.dumps(result.to_safe_dict())
    assert "generation.failed" in [event.event_type for event in result.events]
