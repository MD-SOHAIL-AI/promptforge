from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.agent_runtime import (
    DisabledApiProvider,
    FakeApiProvider,
    ForgeXToolRuntime,
    RuntimeClassification,
    ToolCall,
    ToolName,
    ToolPlan,
    disabled_api_providers,
    fake_smoke_policy,
)
from backend.agent_runtime.tool_policy import SMOKE_CONTENT, SMOKE_PATH, ToolPolicyError
from backend.bridges.diff_service import BridgeDiffService


class PlanProvider:
    provider_id = "fake_api_provider"
    model_id = "test"
    supports_structured_tool_calls = True
    supports_streaming = False
    supports_json_schema = True
    max_input_tokens = 4096
    max_output_tokens = 1024
    production_eligible = False

    def __init__(self, plan, callback=None):
        self.plan = plan
        self.callback = callback

    def request_plan(self, *, task, sandbox_manifest, allowed_tools, policy, run_id):
        del task, sandbox_manifest, allowed_tools, policy, run_id
        if self.callback:
            self.callback()
        return self.plan


def make_runtime(tmp_path: Path, *, pipeline=None, active_setup=None):
    active = tmp_path / "active"
    active.mkdir(parents=True)
    (active / "README.txt").write_text("baseline\n", encoding="utf-8")
    if active_setup:
        active_setup(active)
    runtime = ForgeXToolRuntime(
        managed_sandbox_root=tmp_path / "managed",
        active_workspace_root=active,
        review_service=BridgeDiffService(),
        post_review_pipeline=pipeline,
    )
    return runtime, active


def smoke_call(path=SMOKE_PATH, content=SMOKE_CONTENT):
    return ToolCall(ToolName.WRITE_FILE, {"path": path, "content": content})


def run_plan(tmp_path: Path, plan, **kwargs):
    runtime, active = make_runtime(tmp_path, **kwargs)
    result = runtime.run(task="fake smoke", provider=PlanProvider(plan), policy=fake_smoke_policy())
    return result, active, runtime


def test_fake_provider_returns_structured_tool_call() -> None:
    plan = FakeApiProvider().request_plan(
        task="task",
        sandbox_manifest={"files": ()},
        allowed_tools=["write_file"],
        policy={},
        run_id="fake-run-001",
    )
    assert isinstance(plan, ToolPlan)
    assert plan.calls == (smoke_call(),)


@pytest.mark.parametrize("output", [{"tool": "write_file"}, [], "not-a-plan"])
def test_invalid_provider_output_rejected(tmp_path: Path, output) -> None:
    result, _, _ = run_plan(tmp_path, output)
    assert result.classification is RuntimeClassification.PROVIDER_INVALID
    assert not result.review_created


def test_unknown_tool_rejected() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ToolCall.parse({"tool": "shell", "arguments": {}})


def test_write_file_allowed_only_inside_sandbox(tmp_path: Path) -> None:
    result, active, _ = run_plan(tmp_path, ToolPlan((smoke_call(),)))
    assert result.classification is RuntimeClassification.PASS
    assert not (active / SMOKE_PATH).exists()


@pytest.mark.parametrize("path", ["/absolute.txt", "C:/absolute.txt", "../parent.txt", "safe/../../parent.txt"])
def test_unsafe_paths_rejected(tmp_path: Path, path: str) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(path=path),)))
    assert result.classification is RuntimeClassification.PATH_UNSAFE


@pytest.mark.parametrize("path", ["CON.txt", ".hidden.txt", "safe\\file.txt", "t\u043eken.txt"])
def test_hidden_reserved_and_unicode_paths_rejected(tmp_path: Path, path: str) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(path=path),)))
    assert result.classification is RuntimeClassification.PATH_UNSAFE


def test_symlink_or_reparse_escape_rejected(tmp_path: Path) -> None:
    policy = fake_smoke_policy()
    sandbox = tmp_path / "sandbox"
    outside = tmp_path / "outside"
    sandbox.mkdir()
    outside.mkdir()
    link = sandbox / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host")
    with pytest.raises(ToolPolicyError) as exc:
        policy.resolve_path(sandbox, "link/file.txt", must_exist=False)
    assert exc.value.classification is RuntimeClassification.PATH_UNSAFE


def test_oversized_content_rejected(tmp_path: Path) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(content="x" * 200_000),)))
    assert result.classification is RuntimeClassification.CONTENT_INVALID


def test_wrong_filename_rejected_by_smoke_policy(tmp_path: Path) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(path="WRONG.txt"),)))
    assert result.classification is RuntimeClassification.CONTENT_INVALID


def test_wrong_content_rejected_by_smoke_policy(tmp_path: Path) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(content="wrong\n"),)))
    assert result.classification is RuntimeClassification.CONTENT_INVALID


def test_extra_file_rejected(tmp_path: Path) -> None:
    policy = fake_smoke_policy()
    object.__setattr__(policy, "expected_path", None)
    object.__setattr__(policy, "expected_content", None)
    runtime, _ = make_runtime(tmp_path)
    plan = ToolPlan((smoke_call("ONE.txt", "one\n"), smoke_call("TWO.txt", "two\n")))
    result = runtime.run(task="extra", provider=PlanProvider(plan), policy=policy)
    assert result.classification is RuntimeClassification.EXTRA_CHANGES
    assert not result.review_created


def test_no_changes_rejected(tmp_path: Path) -> None:
    plan = ToolPlan((ToolCall(ToolName.LIST_FILES, {"path": "."}),))
    result, _, _ = run_plan(tmp_path, plan)
    assert result.classification is RuntimeClassification.NO_CHANGES


def test_active_workspace_unchanged_required(tmp_path: Path) -> None:
    runtime, active = make_runtime(tmp_path)
    provider = PlanProvider(ToolPlan((smoke_call(),)), callback=lambda: (active / "README.txt").write_text("mutated\n"))
    result = runtime.run(task="unsafe", provider=provider, policy=fake_smoke_policy())
    assert result.classification is RuntimeClassification.UNSAFE_ABORTED
    assert not result.review_created


def test_sandbox_marker_unchanged_required(tmp_path: Path) -> None:
    runtime, _ = make_runtime(tmp_path)

    def mutate_marker():
        marker = next((tmp_path / "managed").rglob(".forgex-tool-runtime-marker.json"))
        marker.write_text("changed", encoding="utf-8")

    result = runtime.run(task="unsafe", provider=PlanProvider(ToolPlan((smoke_call(),)), mutate_marker), policy=fake_smoke_policy())
    assert result.classification is RuntimeClassification.UNSAFE_ABORTED
    assert not result.review_created


def test_raw_prompt_and_provider_output_are_not_persisted(tmp_path: Path) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(),)))
    payload = result.to_safe_dict()
    assert payload["raw_prompt_persisted"] is False
    assert payload["raw_provider_output_persisted"] is False
    assert "fake smoke" not in str(payload)


def test_credential_files_are_not_read(tmp_path: Path) -> None:
    runtime, _ = make_runtime(tmp_path, active_setup=lambda root: (root / ".env").write_text("SECRET=value\n"))
    plan = ToolPlan((ToolCall(ToolName.READ_FILE, {"path": ".env"}),))
    result = runtime.run(task="read", provider=PlanProvider(plan), policy=fake_smoke_policy())
    assert result.classification in {RuntimeClassification.POLICY_DENIED, RuntimeClassification.PATH_UNSAFE}
    assert not any(path.name == ".env" for path in (tmp_path / "managed").rglob("*"))


@pytest.mark.parametrize("tool", ["shell", "network", "install_dependencies", "apply_patch", "build", "flash"])
def test_unsafe_authority_tools_are_unavailable(tool: str) -> None:
    with pytest.raises(ValueError):
        ToolCall.parse({"tool": tool, "arguments": {}})


def test_apply_build_flash_never_run(tmp_path: Path) -> None:
    result, _, _ = run_plan(tmp_path, ToolPlan((smoke_call(),)))
    assert not result.apply_run and not result.build_run and not result.flash_run


def test_review_created_only_after_exact_pass(tmp_path: Path) -> None:
    passed, _, runtime = run_plan(tmp_path, ToolPlan((smoke_call(),)))
    assert passed.review_created
    review = runtime.review_service.get_review(passed.review_id or "")
    assert len(review.changed_files) == 1
    assert review.artifact_metadata["apply_authority"] == "none"


def test_patch_pipeline_runs_only_after_review(tmp_path: Path) -> None:
    calls: list[str] = []

    def pipeline(review_id: str):
        calls.append(review_id)
        return {"run": True, "export": "pass", "verify": "valid", "preflight": "pass"}

    passed, _, _ = run_plan(tmp_path / "pass", ToolPlan((smoke_call(),)), pipeline=pipeline)
    failed, _, _ = run_plan(tmp_path / "fail", ToolPlan((smoke_call(content="wrong"),)), pipeline=pipeline)
    assert passed.review_created and passed.patch_pipeline["preflight"] == "pass"
    assert not failed.review_created and len(calls) == 1


def test_disabled_api_providers_do_not_execute() -> None:
    providers = disabled_api_providers()
    assert set(providers) == {"anthropic_api", "google_api", "local_model_api"}
    for provider in providers.values():
        assert not provider.enabled and not provider.production_eligible
        with pytest.raises(ValueError, match="design_only"):
            provider.request_plan(task="task", sandbox_manifest={"files": ()}, allowed_tools=[], policy={}, run_id="fake-run-001")


def test_fake_provider_smoke_passes(tmp_path: Path) -> None:
    runtime, _ = make_runtime(tmp_path)
    result = runtime.run(task="smoke", provider=FakeApiProvider(), policy=fake_smoke_policy())
    assert result.classification is RuntimeClassification.PASS
    assert result.created_file_count == 1 and result.review_created


def test_events_are_sanitized(tmp_path: Path) -> None:
    result, active, _ = run_plan(tmp_path, ToolPlan((smoke_call(),)))
    serialized = str([event.to_safe_dict() for event in result.events])
    assert str(active) not in serialized
    assert SMOKE_CONTENT.strip() not in serialized
