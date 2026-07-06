from __future__ import annotations

import asyncio
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from backend.agent_runtime.api_planner_provider import (
    API_PLANNER_CONFIGS,
    API_PRODUCT_SMOKE_CONTENT,
    API_PRODUCT_SMOKE_PATH,
    ApiPlannerClassification,
    ApiPlannerError,
    ApiPlannerProvider,
    ApiTransportHTTPError,
)
from backend.agent_runtime.api_product_runtime_smoke import run_smoke
from backend.agent_runtime.product_agent_service import ProductAgentService, TERMINAL_AGENT_STATUSES
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore


def enabled_env(provider_id: str) -> dict[str, str]:
    config = API_PLANNER_CONFIGS[provider_id]
    env = {
        "FORGEX_ENABLE_AGENT_RUNTIME": "1",
        "FORGEX_ENABLE_API_PROVIDERS": "1",
        "FORGEX_ENABLE_GENERIC_BRIDGE_API": "1",
        "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING": "1",
        config.provider_flag: "1",
        config.api_key_env: "unit-test-key",
    }
    if config.default_model:
        env[config.model_env] = config.default_model
    else:
        env[config.model_env] = "unit-test-model"
    return env


def valid_toolplan_json() -> str:
    return json.dumps({
        "version": "forgex.toolplan.v1",
        "summary": "smoke",
        "tool_calls": [{
            "id": "call_1",
            "type": "write_file",
            "path": API_PRODUCT_SMOKE_PATH,
            "content": API_PRODUCT_SMOKE_CONTENT,
        }],
        "final": False,
    })


def envelope(content: str) -> dict[str, object]:
    return {"choices": [{"message": {"content": content}}]}


@pytest.mark.parametrize("provider_id", ["gemini", "groq", "openrouter", "openai", "nvidia_nim"])
def test_api_providers_disabled_by_default(provider_id: str) -> None:
    registry = ProductProviderRegistry(env={})
    status = next(item for item in registry.safe_statuses() if item["provider_id"] == provider_id)
    assert status["state"] == ApiPlannerClassification.DISABLED.value
    assert status["routeable"] is False
    assert status["enabled_by_default"] is False


@pytest.mark.parametrize("provider_id", ["gemini", "groq", "openrouter", "openai", "nvidia_nim"])
def test_api_provider_requires_specific_flag(provider_id: str) -> None:
    env = enabled_env(provider_id)
    env.pop(API_PLANNER_CONFIGS[provider_id].provider_flag)
    with pytest.raises(ValueError, match=ApiPlannerClassification.DISABLED.value):
        ProductProviderRegistry(env=env, confirm_real_api=True).resolve(provider_id)


@pytest.mark.parametrize("provider_id", ["gemini", "groq", "openrouter", "openai", "nvidia_nim"])
def test_api_provider_missing_key_blocks_before_request(provider_id: str) -> None:
    env = enabled_env(provider_id)
    env[API_PLANNER_CONFIGS[provider_id].api_key_env] = ""
    if API_PLANNER_CONFIGS[provider_id].fallback_api_key_env:
        env[API_PLANNER_CONFIGS[provider_id].fallback_api_key_env] = ""
    summary = asyncio.run(run_smoke(provider_id=provider_id, confirmed=True, env=env))
    assert summary["classification"] == ApiPlannerClassification.KEY_MISSING.value
    assert summary["outbound_request_count"] == 0
    assert summary["review_created"] is False


def test_nvidia_provider_missing_model_blocks_before_request() -> None:
    env = enabled_env("nvidia_nim")
    env["FORGEX_NVIDIA_NIM_MODEL"] = ""
    summary = asyncio.run(run_smoke(provider_id="nvidia_nim", confirmed=True, env=env))
    assert summary["classification"] == ApiPlannerClassification.MODEL_MISSING.value
    assert summary["outbound_request_count"] == 0


def test_real_smoke_requires_confirm_flag() -> None:
    summary = asyncio.run(run_smoke(provider_id="gemini", confirmed=False, env=enabled_env("gemini")))
    assert summary["classification"] == ApiPlannerClassification.CONFIRMATION_REQUIRED.value
    assert summary["outbound_request_count"] == 0


@pytest.mark.parametrize("provider_id", ["gemini", "groq", "openrouter", "openai", "nvidia_nim"])
def test_mocked_api_request_shape_and_toolplan_parse(provider_id: str) -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, payload: dict[str, object], api_key: str, headers: dict[str, str], timeout: float) -> dict[str, object]:
        captured.update(url=url, payload=payload, api_key=api_key, headers=headers, timeout=timeout)
        return envelope(valid_toolplan_json())

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS[provider_id], env=enabled_env(provider_id), confirmed=True, transport=transport)
    plan = provider.request_turn(task="write smoke", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert plan.tool_calls[0].path == API_PRODUCT_SMOKE_PATH
    assert captured["url"].startswith("https://")
    assert captured["payload"]["model"] == provider.model_id
    assert captured["payload"]["stream"] is False
    assert captured["api_key"] == "unit-test-key"
    metadata = provider.to_safe_metadata()
    assert "unit-test-key" not in json.dumps(metadata)
    assert provider.outbound_request_count == 1
    assert metadata["request_reached_provider"] is True


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, "", ApiPlannerClassification.AUTH_INVALID),
        (403, "billing required", ApiPlannerClassification.BILLING_REQUIRED),
        (404, "", ApiPlannerClassification.MODEL_UNAVAILABLE),
        (429, "rate limit", ApiPlannerClassification.RATE_LIMITED),
        (429, "insufficient_quota", ApiPlannerClassification.QUOTA_EXCEEDED),
    ],
)
def test_http_errors_map_to_safe_classifications(status: int, body: str, expected: ApiPlannerClassification) -> None:
    def transport(*args: object, **kwargs: object) -> dict[str, object]:
        raise ApiTransportHTTPError(status, body, "req-safe-123")

    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], env=enabled_env("openai"), confirmed=True, transport=transport)
    with pytest.raises(ApiPlannerError) as exc:
        provider.request_turn(task="write smoke", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert exc.value.classification == expected.value
    metadata = provider.to_safe_metadata()
    assert metadata["request_reached_provider"] is True
    assert metadata["http_status"] == status
    assert metadata["provider_request_id"] == "req-safe-123"


def test_network_failure_is_not_reported_as_provider_rate_limit() -> None:
    def transport(*args: object, **kwargs: object) -> dict[str, object]:
        raise urllib.error.URLError("connection refused")

    provider = ApiPlannerProvider(
        API_PLANNER_CONFIGS["openrouter"],
        env=enabled_env("openrouter"),
        confirmed=True,
        transport=transport,
    )
    with pytest.raises(ApiPlannerError) as exc:
        provider.request_turn(task="write smoke", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)

    assert exc.value.classification == ApiPlannerClassification.NETWORK_ERROR.value
    assert provider.to_safe_metadata()["request_reached_provider"] is False


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("not json", ApiPlannerClassification.RESPONSE_INVALID),
        (json.dumps({"version": "forgex.toolplan.v1", "summary": "x", "tool_calls": [], "final": False}), ApiPlannerClassification.TOOLPLAN_INVALID),
        (json.dumps({"version": "forgex.toolplan.v1", "summary": "x", "tool_calls": [{"id": "call_1", "type": "shell", "path": "x", "content": "x"}], "final": False}), ApiPlannerClassification.TOOLPLAN_UNSAFE),
        (json.dumps({"version": "forgex.toolplan.v1", "summary": "x", "tool_calls": [{"id": "call_1", "type": "write_file", "path": "../unsafe.txt", "content": API_PRODUCT_SMOKE_CONTENT}], "final": False}), ApiPlannerClassification.TOOLPLAN_UNSAFE),
        (json.dumps({"version": "forgex.toolplan.v1", "summary": "x", "tool_calls": [{"id": "call_1", "type": "write_file", "path": API_PRODUCT_SMOKE_PATH, "content": "wrong"}], "final": False}), ApiPlannerClassification.TOOLPLAN_UNSAFE),
    ],
)
def test_invalid_model_output_fails_closed(content: str, expected: ApiPlannerClassification) -> None:
    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["gemini"], env=enabled_env("gemini"), confirmed=True, transport=lambda *args: envelope(content))
    with pytest.raises(ApiPlannerError) as exc:
        provider.request_turn(task="write smoke", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)
    assert exc.value.classification == expected.value


def test_valid_api_planner_flows_through_product_runtime_and_creates_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = tmp_path
        active = root / "active"
        active.mkdir()
        (active / "README.txt").write_text("unchanged\n", encoding="utf-8")
        reviews = BridgeDiffService(store=BridgeReviewStore(
            snapshots_path=root / "state" / "snapshots.jsonl",
            reviews_path=root / "state" / "reviews.jsonl",
        ))
        registry = ProductProviderRegistry(
            env=enabled_env("gemini"),
            confirm_real_api=True,
            transport=lambda *args: envelope(valid_toolplan_json()),
        )
        service = ProductAgentService(
            managed_sandbox_root=root / "sandboxes",
            review_service=reviews,
            provider_registry=registry,
            enabled=True,
        )
        run = await service.start_run(
            project_id="project",
            active_workspace_root=active,
            instruction="create smoke",
            provider_id="gemini",
        )
        for _ in range(1000):
            run = service.get_run(run.run_id)
            if run.status in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(0.01)
        assert run.status == "completed"
        assert run.classification == ApiPlannerClassification.SANDBOX_WRITE_PASS.value
        assert run.review_id is not None
        assert (run.created_file_count, run.modified_file_count, run.deleted_file_count) == (1, 0, 0)
        assert run.active_workspace_unchanged is True
        assert not (active / API_PRODUCT_SMOKE_PATH).exists()
        review = reviews.get_review(run.review_id)
        assert review.provider_id == "gemini"
        await service.close()

    asyncio.run(scenario())


def test_greeting_is_answered_locally_without_provider_request(tmp_path: Path) -> None:
    async def scenario() -> None:
        active = tmp_path / "active"
        active.mkdir()
        calls = 0

        def transport(*args: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            raise AssertionError("greeting must not call provider")

        reviews = BridgeDiffService(store=BridgeReviewStore(
            snapshots_path=tmp_path / "state" / "snapshots.jsonl",
            reviews_path=tmp_path / "state" / "reviews.jsonl",
        ))
        service = ProductAgentService(
            managed_sandbox_root=tmp_path / "sandboxes",
            review_service=reviews,
            provider_registry=ProductProviderRegistry(
                env=enabled_env("openrouter"), confirm_real_api=True, transport=transport,
            ),
            enabled=True,
        )

        run = await service.start_run(
            project_id="project", active_workspace_root=active,
            instruction="hi", provider_id="openrouter",
        )

        assert run.status == "completed"
        assert run.classification == "LOCAL_RESPONSE"
        assert run.actual_provider_id == "forgex_local"
        assert run.outbound_request_count == 0
        assert calls == 0
        assert "Describe the ESP32" in (run.to_safe_dict()["assistant_message"] or "")
        await service.close()

    asyncio.run(scenario())


def test_rate_limited_provider_falls_back_once_then_enters_cooldown(tmp_path: Path) -> None:
    async def scenario() -> None:
        active = tmp_path / "active"
        active.mkdir()
        calls = 0

        def transport(*args: object) -> dict[str, object]:
            nonlocal calls
            calls += 1
            raise ApiTransportHTTPError(429, "rate limit", "req-test-429")

        reviews = BridgeDiffService(store=BridgeReviewStore(
            snapshots_path=tmp_path / "state" / "snapshots.jsonl",
            reviews_path=tmp_path / "state" / "reviews.jsonl",
        ))
        registry = ProductProviderRegistry(
            env=enabled_env("openrouter"), confirm_real_api=True, transport=transport,
        )
        service = ProductAgentService(
            managed_sandbox_root=tmp_path / "sandboxes",
            review_service=reviews,
            provider_registry=registry,
            enabled=True,
        )

        first = await service.start_run(
            project_id="project", active_workspace_root=active,
            instruction="Create an ESP32 WiFi device monitor",
            provider_id="openrouter", fallback_provider_id="verified_template",
        )
        for _ in range(1000):
            first = service.get_run(first.run_id)
            if first.status in TERMINAL_AGENT_STATUSES:
                break
            await asyncio.sleep(0.01)
        assert first.status == "completed"
        assert first.actual_provider_id == "verified_template"
        assert first.fallback_reason == ApiPlannerClassification.RATE_LIMITED.value
        assert first.http_status == 429
        assert first.provider_request_id == "req-test-429"
        assert calls == 1

        second = await service.start_run(
            project_id="project", active_workspace_root=active,
            instruction="Do an unsupported custom task",
            provider_id="openrouter",
        )
        assert second.status == "failed"
        assert second.classification == "API_RATE_LIMITED_COOLDOWN"
        assert calls == 1
        await service.close()

    asyncio.run(scenario())
