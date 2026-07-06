from __future__ import annotations

import threading
import asyncio
import time
from pathlib import Path

import pytest

from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.agent_runtime.product_providers import FakeProductPlanner
from backend.agent_runtime.product_agent_service import ProductAgentService, TERMINAL_AGENT_STATUSES
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan, RuntimeClassification
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.agent_runtime.tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore


def runtime(tmp_path: Path) -> tuple[ForgeXToolRuntime, BridgeDiffService, Path]:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.txt").write_text("unchanged\n", encoding="utf-8")
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "snapshots.jsonl",
        reviews_path=tmp_path / "state" / "reviews.jsonl",
    ))
    return ForgeXToolRuntime(managed_sandbox_root=tmp_path / "sandboxes", active_workspace_root=active, review_service=reviews), reviews, active


def test_general_product_toolplan_schema_is_strict() -> None:
    plan = ProductToolPlan.parse({
        "version": PRODUCT_TOOLPLAN_VERSION, "summary": "write", "final": False,
        "tool_calls": [{"id": "call_1", "type": "write_file", "path": "safe.txt", "content": "safe\n"}],
    })
    assert plan.tool_calls[0].path == "safe.txt"
    with pytest.raises(ValueError):
        ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "x", "final": True, "tool_calls": [], "extra": True})
    with pytest.raises(ValueError):
        ProductToolPlan.parse_json("```json\n{}\n```")


def test_fake_product_loop_returns_tool_result_and_persists_review(tmp_path: Path) -> None:
    owner, reviews, active = runtime(tmp_path)
    before = (active / "README.txt").read_bytes()
    result = owner.run_product(task="safe fake task", planner=FakeProductPlanner(), policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    assert (result.created_file_count, result.modified_file_count, result.deleted_file_count) == (1, 0, 0)
    assert result.review_id and reviews.get_review(result.review_id).artifact_source == "forgex_product_agent_runtime"
    assert (active / "README.txt").read_bytes() == before
    assert not (active / "FORGEX_AGENT_RUNTIME_SMOKE.txt").exists()


class RecordingPlanner(FakeProductPlanner):
    def __init__(self) -> None:
        self.results = ()

    def request_turn(self, **kwargs):
        self.results = kwargs["tool_results"]
        return super().request_turn(**kwargs)


def test_iterative_loop_returns_sanitized_tool_result_to_planner(tmp_path: Path) -> None:
    owner, _, _ = runtime(tmp_path)
    planner = RecordingPlanner()
    result = owner.run_product(task="safe", planner=planner, policy=product_agent_policy())
    assert result.classification is RuntimeClassification.PASS
    assert planner.results[-1]["status"] == "completed"
    assert "content" not in planner.results[-1]


class EndlessPlanner:
    provider_id = "endless"
    model_id = "fake"

    def request_turn(self, **kwargs):
        return ProductToolPlan.parse({
            "version": PRODUCT_TOOLPLAN_VERSION, "summary": "continue", "final": False,
            "tool_calls": [{"id": f"call_{kwargs['turn']}", "type": "write_file", "path": f"file{kwargs['turn']}.txt", "content": "x\n"}],
        })


def test_loop_stops_at_max_turns_without_review(tmp_path: Path) -> None:
    owner, reviews, _ = runtime(tmp_path)
    result = owner.run_product(task="bounded", planner=EndlessPlanner(), policy=product_agent_policy(), limits=ProductRuntimeLimits(max_turns=2))
    assert result.classification is RuntimeClassification.MAX_TURNS
    assert result.review_created is False
    assert reviews.list_reviews() == ()


class DeniedPlanner:
    provider_id = "denied"
    model_id = "fake"

    def request_turn(self, **kwargs):
        if kwargs["turn"] == 1:
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION, "summary": "unsafe", "final": False,
                "tool_calls": [{"id": "bad", "type": "shell", "path": "safe.txt", "content": "cmd"}],
            })
        assert kwargs["tool_results"][-1]["status"] == "denied"
        return ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "stop", "final": True, "tool_calls": []})


def test_denied_tool_returns_sanitized_result_and_never_creates_review(tmp_path: Path) -> None:
    owner, reviews, _ = runtime(tmp_path)
    result = owner.run_product(task="deny", planner=DeniedPlanner(), policy=product_agent_policy())
    assert result.classification is RuntimeClassification.POLICY_DENIED
    assert result.tool_execution_count == 0
    assert reviews.list_reviews() == ()


def test_cancelled_run_executes_nothing(tmp_path: Path) -> None:
    owner, reviews, _ = runtime(tmp_path)
    cancelled = threading.Event(); cancelled.set()
    result = owner.run_product(task="cancel", planner=FakeProductPlanner(), policy=product_agent_policy(), cancel_event=cancelled)
    assert result.classification is RuntimeClassification.CANCELLED
    assert result.tool_execution_count == 0
    assert reviews.list_reviews() == ()


@pytest.mark.parametrize("provider_id", ["agy_local_cli_paused", "codex_local_cli_paused", "opencode_reference_only", "openai_api_planner"])
def test_non_routeable_product_providers_cannot_execute(provider_id: str) -> None:
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        ProductProviderRegistry(fake_enabled=True).resolve(provider_id)


def test_fake_provider_requires_explicit_feature_enable() -> None:
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        ProductProviderRegistry(fake_enabled=False).resolve("fake_planner")


class SlowPlanner(FakeProductPlanner):
    def request_turn(self, **kwargs):
        time.sleep(0.1)
        return super().request_turn(**kwargs)


class SlowRegistry:
    def resolve(self, provider_id: str):
        assert provider_id == "fake_planner"
        return SlowPlanner()


def test_product_service_cancel_stops_before_tool_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, reviews, active = runtime(tmp_path)
        service = ProductAgentService(
            managed_sandbox_root=tmp_path / "service-sandboxes",
            review_service=reviews,
            provider_registry=SlowRegistry(),  # type: ignore[arg-type]
            enabled=True,
        )
        run = await service.start_run(project_id="project", active_workspace_root=active, instruction="cancel")
        service.cancel(run.run_id)
        for _ in range(100):
            if service.get_run(run.run_id).status in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(0.01)
        completed = service.get_run(run.run_id)
        assert completed.status == "cancelled"
        assert completed.tool_execution_count == 0
        assert completed.review_id is None
        await service.close()

    asyncio.run(scenario())
