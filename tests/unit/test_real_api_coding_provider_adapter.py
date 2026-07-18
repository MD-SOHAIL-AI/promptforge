from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_provider_contracts import CodingProviderFailureCode, CodingRunStatus
from backend.agent_runtime.real_api_coding_provider_adapter import RealApiCodingProviderAdapter
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.model_router import ModelRequest, ModelResponse
from backend.services.llm_service import LLMConfigurationError, LLMProvider, LLMProviderError


class StubModelRouter:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.content = content or proposal_json()
        self.exc = exc
        self.requests: list[ModelRequest] = []

    async def generate_model(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return ModelResponse(
            content=self.content,
            provider_id=request.provider_id or "openrouter",
            model_id=request.model_id or "test-model",
            input_tokens=12,
            output_tokens=34,
            total_tokens=46,
        )


def proposal_json(*, path: str = "src/main.cpp", content: str = "void setup() {}\nvoid loop() {}\n") -> str:
    return json.dumps({
        "schema_version": "forgex.api_coding_agent.v1",
        "summary": "Update firmware source.",
        "files": [{"path": path, "action": "create_or_update", "content": content}],
        "commands_suggested": [{"command": "echo should-not-run > executed.txt", "reason": "Advisory only"}],
        "risks": ["Review before flashing."],
        "next_steps": ["Approve apply if acceptable."],
    })


def make_service(tmp_path: Path) -> tuple[ApiCodingAgentService, BridgeDiffService]:
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "snapshots.jsonl",
        reviews_path=tmp_path / "state" / "reviews.jsonl",
    ))
    service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(tmp_path / "managed" / "api-coding-agent-sandboxes"),
        review_service=reviews,
        enabled=False,
    )
    return service, reviews


def make_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "active"
    active.mkdir()
    (active / "platformio.ini").write_text("[env:esp32dev]\n", encoding="utf-8")
    (active / "src").mkdir()
    (active / "src" / "main.cpp").write_text("old\n", encoding="utf-8")
    (active / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    return active


@pytest.mark.asyncio
async def test_real_api_adapter_builds_bounded_context_and_creates_review_without_active_mutation(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, reviews = make_service(tmp_path)
    router = StubModelRouter()

    result = await RealApiCodingProviderAdapter(
        model_router=router,
        review_service=service,
        env={"FORGEX_ENABLE_REAL_API_CODING_AGENT": "1"},
    ).generate_review(
        prompt="update blink",
        workspace_path=active,
        selected_files=["platformio.ini", "src/main.cpp", ".env"],
        provider_id="openrouter",
        model_route="openai/gpt-test",
        run_id="coding-workflow-real-test",
    )

    assert result.status is CodingRunStatus.AWAITING_APPLY
    assert result.review_id
    assert result.files_changed == ("src/main.cpp",)
    assert result.metadata["context_file_count"] == 2
    assert result.metadata["context_excluded_count"] >= 1
    assert result.metadata["command_suggestion_count"] == 1
    assert [event.event_type for event in result.events] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
    ]
    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.provider_id == "openrouter"
    assert request.model_id == "openai/gpt-test"
    assert request.system_prompt is not None
    assert "Return ONLY valid JSON." in request.system_prompt
    assert "Do not use markdown fences." in request.system_prompt
    assert "Do not include prose before or after JSON." in request.system_prompt
    assert "schema_version = forgex.api_coding_agent.v1" in request.system_prompt
    assert "Use relative paths only." in request.system_prompt
    assert "Do not include secrets." in request.system_prompt
    assert "Do not include command execution." in request.system_prompt
    assert "Do not modify .env, .git, generated folders, or protected paths." in request.system_prompt
    assert "Return ONLY valid JSON." in request.prompt
    assert "src/main.cpp" in request.prompt
    assert "OPENAI_API_KEY" not in request.prompt
    assert "sk-secret" not in request.prompt
    assert (active / "src" / "main.cpp").read_text(encoding="utf-8") == "old\n"
    assert not (active / "executed.txt").exists()
    review = reviews.get_review(result.review_id)
    assert tuple(item.path for item in review.changed_files) == result.files_changed


@pytest.mark.asyncio
async def test_real_api_adapter_disabled_flag_blocks_model_call(tmp_path: Path) -> None:
    active = make_workspace(tmp_path)
    service, _ = make_service(tmp_path)
    router = StubModelRouter()

    result = await RealApiCodingProviderAdapter(
        model_router=router,
        review_service=service,
        env={},
    ).generate_review(prompt="update", workspace_path=active)

    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is CodingProviderFailureCode.REAL_API_CODING_AGENT_DISABLED
    assert router.requests == []


@pytest.mark.asyncio
async def test_real_api_adapter_empty_context_fails_before_model_call(tmp_path: Path) -> None:
    active = tmp_path / "empty"
    active.mkdir()
    service, _ = make_service(tmp_path)
    router = StubModelRouter()

    result = await RealApiCodingProviderAdapter(
        model_router=router,
        review_service=service,
        env={"FORGEX_ENABLE_REAL_API_CODING_AGENT": "1"},
    ).generate_review(prompt="update", workspace_path=active)

    assert result.failure_code is CodingProviderFailureCode.API_CODING_CONTEXT_EMPTY
    assert router.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "failure_code"),
    [
        ("Here is JSON: {}", CodingProviderFailureCode.API_CONTRACT_INVALID),
        (f"```json\n{proposal_json()}\n```", CodingProviderFailureCode.API_CONTRACT_INVALID),
        (json.dumps({
            "schema_version": "wrong",
            "summary": "bad",
            "files": [],
            "commands_suggested": [],
            "risks": [],
            "next_steps": [],
        }), CodingProviderFailureCode.API_CONTRACT_INVALID),
        (proposal_json(path="../evil.txt"), CodingProviderFailureCode.UNSAFE_OUTPUT),
        (proposal_json(content="x" * (64 * 1024 + 1)), CodingProviderFailureCode.OVERSIZED_OUTPUT),
    ],
    ids=["prose", "fenced-json", "wrong-schema", "unsafe-path", "oversized"],
)
async def test_real_api_adapter_rejects_invalid_model_output(
    tmp_path: Path,
    content: str,
    failure_code: CodingProviderFailureCode,
) -> None:
    active = make_workspace(tmp_path)
    service, reviews = make_service(tmp_path)

    result = await RealApiCodingProviderAdapter(
        model_router=StubModelRouter(content),
        review_service=service,
        env={"FORGEX_ENABLE_REAL_API_CODING_AGENT": "1"},
    ).generate_review(prompt="update", workspace_path=active)

    assert result.status is CodingRunStatus.FAILED
    assert result.failure_code is failure_code
    assert result.events[-1].event_type == "generation.failed"
    assert reviews.list_reviews() == ()
    assert (active / "src" / "main.cpp").read_text(encoding="utf-8") == "old\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "failure_code"),
    [
        (LLMConfigurationError("missing", provider=LLMProvider.OPENAI), CodingProviderFailureCode.REAL_API_CODING_AGENT_UNAVAILABLE),
        (LLMProviderError("provider failed with sk-secret", provider=LLMProvider.OPENAI), CodingProviderFailureCode.API_CODING_MODEL_CALL_FAILED),
    ],
    ids=["configuration", "provider-error"],
)
async def test_real_api_adapter_maps_model_router_failures_to_safe_errors(
    tmp_path: Path,
    exc: Exception,
    failure_code: CodingProviderFailureCode,
) -> None:
    active = make_workspace(tmp_path)
    service, _ = make_service(tmp_path)

    result = await RealApiCodingProviderAdapter(
        model_router=StubModelRouter(exc=exc),
        review_service=service,
        env={"FORGEX_ENABLE_REAL_API_CODING_AGENT": "1"},
    ).generate_review(prompt="update", workspace_path=active)

    assert result.failure_code is failure_code
    assert "sk-secret" not in result.safe_message
    assert "Traceback" not in result.safe_message
