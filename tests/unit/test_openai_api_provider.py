from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_runtime.api_provider_smoke import main, run_smoke
from backend.agent_runtime.openai_api_provider import (
    API_KEY_ENV,
    DEFAULT_OPENAI_MODEL,
    PROVIDER_FLAG,
    SPIKE_FLAG,
    OpenAIApiProvider,
)
from backend.agent_runtime.provider_contracts import ApiProviderClassification, ApiProviderError
from backend.agent_runtime.tool_contracts import ToolName, ToolPlan
from backend.agent_runtime.tool_policy import API_SMOKE_CONTENT, API_SMOKE_PATH
from backend.agent_runtime.tool_policy import api_provider_smoke_policy
from backend.agent_runtime.tool_runtime import ForgeXToolRuntime
from backend.bridges.diff_service import BridgeDiffService


ALLOWED_TOOLS = [
    ToolName.LIST_FILES.value,
    ToolName.READ_FILE.value,
    ToolName.WRITE_FILE.value,
    ToolName.EDIT_FILE_SIMPLE.value,
]


def enabled_env(**overrides: str) -> dict[str, str]:
    values = {
        SPIKE_FLAG: "1",
        PROVIDER_FLAG: "1",
        API_KEY_ENV: "test-only-key-never-send",
    }
    values.update(overrides)
    return values


def response_for(payload: object) -> dict[str, object]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ]
    }


def valid_payload(path: str = API_SMOKE_PATH, content: str = API_SMOKE_CONTENT) -> dict[str, object]:
    return {"tool_calls": [{"tool": "write_file", "arguments": {"path": path, "content": content}}]}


def provider_for(payload: object, *, env=None) -> OpenAIApiProvider:
    return OpenAIApiProvider(
        confirm_real_api=True,
        env=env or enabled_env(),
        transport=lambda url, body, key, timeout: response_for(payload),
    )


def request(provider: OpenAIApiProvider) -> ToolPlan:
    return provider.request_plan(
        task="Create the exact smoke file.",
        sandbox_manifest={"files": ("README.txt",)},
        allowed_tools=ALLOWED_TOOLS,
        policy={"sandbox_only": True},
        run_id="api-run-001",
    )


def test_openai_provider_disabled_without_flags() -> None:
    provider = OpenAIApiProvider(confirm_real_api=True, env={API_KEY_ENV: "not-used"}, transport=lambda *args: pytest.fail())
    with pytest.raises(ApiProviderError) as exc:
        request(provider)
    assert exc.value.classification is ApiProviderClassification.DISABLED


def test_openai_provider_refuses_without_confirmation(capsys) -> None:
    assert main(["--provider", "openai"], env=enabled_env()) == 2
    assert "API_PROVIDER_DISABLED" in capsys.readouterr().out


def test_openai_provider_reports_key_missing_without_request() -> None:
    called = False

    def transport(*args):
        nonlocal called
        called = True
        return response_for(valid_payload())

    summary = run_smoke(provider_name="openai", confirmed=True, env=enabled_env(**{API_KEY_ENV: ""}), transport=transport)
    assert summary.classification is ApiProviderClassification.KEY_MISSING
    assert not called and not summary.execution_attempted
    assert not summary.api_key_detected and not summary.outbound_request_attempted
    assert summary.outbound_request_count == 0
    assert summary.tool_execution_count == 0 and not summary.review_created


def test_api_key_is_not_printed_or_persisted(tmp_path: Path, capsys) -> None:
    secret = "test-secret-key-that-must-not-appear"
    summary = run_smoke(
        provider_name="openai",
        confirmed=True,
        env=enabled_env(**{API_KEY_ENV: secret}),
        transport=lambda *args: response_for(valid_payload()),
    )
    print(json.dumps(summary.to_safe_dict(), sort_keys=True))
    assert secret not in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []
    assert summary.api_key_detected is True
    assert summary.api_key_printed is False and summary.api_key_persisted is False


def test_raw_prompt_and_response_are_not_in_safe_metadata() -> None:
    provider = provider_for(valid_payload())
    request(provider)
    serialized = json.dumps(provider.to_safe_metadata(), sort_keys=True)
    assert "Create the exact smoke file" not in serialized
    assert API_SMOKE_CONTENT.strip() not in serialized
    assert set(provider.to_safe_metadata()) == {
        "provider_id", "model_id", "run_id", "tool_count", "classification", "safe_error_code",
        "outbound_request_count",
    }


def test_valid_model_json_becomes_internal_tool_plan() -> None:
    captured: dict[str, object] = {}

    def transport(url, body, key, timeout):
        captured.update(url=url, body=body, key_seen=bool(key), timeout=timeout)
        return response_for(valid_payload())

    provider = OpenAIApiProvider(confirm_real_api=True, env=enabled_env(), transport=transport)
    plan = request(provider)
    assert plan.calls[0].tool is ToolName.WRITE_FILE
    assert plan.calls[0].arguments == {"path": API_SMOKE_PATH, "content": API_SMOKE_CONTENT}
    assert provider.model_id == DEFAULT_OPENAI_MODEL
    assert captured["body"]["store"] is False
    assert captured["body"]["text"]["format"]["strict"] is True


@pytest.mark.parametrize("output", ["not-json", "```json\n{}\n```"])
def test_invalid_or_code_fenced_json_is_rejected(output: str) -> None:
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(output))
    assert exc.value.classification is ApiProviderClassification.INVALID_JSON


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"tool_calls": [], "extra": True},
        {"tool_calls": [{"tool": "unknown", "arguments": {}}]},
        {"tool_calls": [valid_payload()["tool_calls"][0], valid_payload()["tool_calls"][0]]},
    ],
)
def test_invalid_tool_plan_schema_is_rejected(payload: object) -> None:
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(payload))
    assert exc.value.classification is ApiProviderClassification.SCHEMA_INVALID


@pytest.mark.parametrize("tool", ["shell", "network", "install_dependencies", "apply_patch", "build", "flash"])
def test_model_authority_requests_are_policy_denied(tool: str) -> None:
    payload = {"tool_calls": [{"tool": tool, "arguments": {}}]}
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(payload))
    assert exc.value.classification is ApiProviderClassification.POLICY_DENIED


@pytest.mark.parametrize("path", ["C:/outside.txt", "/outside.txt", "../outside.txt", "safe/../../outside.txt"])
def test_absolute_drive_and_parent_paths_are_rejected(path: str) -> None:
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(valid_payload(path=path)))
    assert exc.value.classification is ApiProviderClassification.PATH_UNSAFE


@pytest.mark.parametrize(
    ("path", "content"),
    [("WRONG.txt", API_SMOKE_CONTENT), (API_SMOKE_PATH, "wrong\n")],
)
def test_wrong_filename_and_content_are_rejected(path: str, content: str) -> None:
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(valid_payload(path, content)))
    assert exc.value.classification is ApiProviderClassification.CONTENT_INVALID


def test_oversized_content_is_rejected() -> None:
    with pytest.raises(ApiProviderError) as exc:
        request(provider_for(valid_payload(content="x" * 200_000)))
    assert exc.value.classification in {
        ApiProviderClassification.CONTENT_INVALID,
        ApiProviderClassification.MODEL_ERROR,
    }


def test_mocked_openai_smoke_flows_through_runtime_and_creates_review() -> None:
    summary = run_smoke(
        provider_name="openai",
        confirmed=True,
        env=enabled_env(),
        transport=lambda *args: response_for(valid_payload()),
    )
    assert summary.classification is ApiProviderClassification.PASS
    assert summary.execution_attempted and summary.tool_execution_count == 1
    assert summary.outbound_request_attempted and summary.outbound_request_count == 1
    assert summary.tool_plan_valid and summary.api_key_detected
    assert summary.created_file_count == 1 and summary.review_created
    assert summary.modified_file_count == 0 and summary.deleted_file_count == 0
    assert summary.expected_file_created and summary.content_exact
    assert summary.active_workspace_unchanged and summary.marker_unchanged
    assert not summary.apply_run and not summary.build_run and not summary.flash_run
    assert not summary.patch_export_run and not summary.patch_verify_run and not summary.patch_preflight_run


def test_api_runtime_events_are_sanitized(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.txt").write_text("baseline\n", encoding="utf-8")
    runtime = ForgeXToolRuntime(
        managed_sandbox_root=tmp_path / "managed",
        active_workspace_root=active,
        review_service=BridgeDiffService(),
    )
    result = runtime.run(task="Create exact smoke.", provider=provider_for(valid_payload()), policy=api_provider_smoke_policy())
    event_types = {event.event_type.value for event in result.events}
    assert {
        "api_provider.request.started",
        "api_provider.request.completed",
        "api_provider.plan.parsed",
        "tool_runtime.validation.started",
        "tool_runtime.validation.completed",
        "tool_runtime.execution.completed",
        "review.candidate.created",
        "api_provider.smoke.completed",
    }.issubset(event_types)
    serialized = json.dumps([event.to_safe_dict() for event in result.events])
    assert API_SMOKE_CONTENT.strip() not in serialized
    assert str(active) not in serialized
    review = runtime.review_service.get_review(result.review_id or "")
    assert review.artifact_metadata == {
        "provider_kind": "api",
        "provider_id": "openai_api",
        "model_id": DEFAULT_OPENAI_MODEL,
        "runtime": "forgex_owned_tool_runtime",
        "tool_count": 1,
        "tools_executed": "write_file",
        "created_files": 1,
        "modified_files": 0,
        "deleted_files": 0,
        "apply_authority": "none",
        "build_authority": "none",
        "flash_authority": "none",
        "raw_prompt_persisted": False,
        "raw_response_persisted": False,
        "api_key_persisted": False,
    }


def test_review_and_patch_pipeline_not_run_for_invalid_output() -> None:
    summary = run_smoke(
        provider_name="openai",
        confirmed=True,
        env=enabled_env(),
        transport=lambda *args: response_for("invalid"),
    )
    assert summary.classification is ApiProviderClassification.INVALID_JSON
    assert not summary.review_created and summary.tool_execution_count == 0
    assert not summary.patch_export_run


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        (ApiProviderClassification.RATE_LIMITED, ApiProviderClassification.RATE_LIMITED),
        (ApiProviderClassification.TIMEOUT, ApiProviderClassification.TIMEOUT),
        (ApiProviderClassification.MODEL_ERROR, ApiProviderClassification.MODEL_ERROR),
    ],
)
def test_provider_transport_failures_are_safely_classified(classification, expected) -> None:
    def transport(*args):
        raise ApiProviderError(classification, "safe_transport_failure")

    provider = OpenAIApiProvider(confirm_real_api=True, env=enabled_env(), transport=transport)
    with pytest.raises(ApiProviderError) as exc:
        request(provider)
    assert exc.value.classification is expected
