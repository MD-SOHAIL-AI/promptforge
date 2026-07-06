from __future__ import annotations

from pathlib import Path

import pytest

from backend.provider_runtime import (
    ForgeXRunSummary,
    GeneratedArtifactValidator,
    GenerationStatus,
    ProviderCatalog,
    ProviderType,
    WorkspaceMode,
    detect_workspace_mode,
)
from backend.provider_runtime.templates import match_template
from backend.agent_runtime.product_agent_service import ProductAgentService, TERMINAL_AGENT_STATUSES
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore


def test_catalog_separates_provider_categories() -> None:
    catalog = ProviderCatalog()

    assert catalog.get("openai").provider_type is ProviderType.API
    assert catalog.get("codex").provider_type is ProviderType.AGENT
    assert catalog.get("verified_template").provider_type is ProviderType.TEMPLATE
    assert catalog.get("anthropic").enabled is False
    assert catalog.get("cerebras").enabled is False


def test_workspace_mode_detects_existing_platformio_project(tmp_path: Path) -> None:
    assert detect_workspace_mode(tmp_path) is WorkspaceMode.GENERATE_INTO_FOLDER
    (tmp_path / "platformio.ini").write_text("[env:test]\n", encoding="utf-8")
    assert detect_workspace_mode(tmp_path) is WorkspaceMode.MODIFY_EXISTING


def test_artifact_validation_classifies_hashes_and_no_op(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.cpp").write_bytes(b"same\n")
    result = GeneratedArtifactValidator().validate(
        tmp_path,
        {"src/main.cpp": "same\n", "platformio.ini": "[env:test]\n"},
        required_files=("platformio.ini", "src/main.cpp"),
    )

    assert result.valid is True
    assert result.created_files == ("platformio.ini",)
    assert result.unchanged_files == ("src/main.cpp",)

    no_op = GeneratedArtifactValidator().validate(tmp_path, {"src/main.cpp": "same\n"}, required_files=("src/main.cpp",))
    assert no_op.no_op_success is True


@pytest.mark.parametrize("path", ("../escape.txt", "C:\\escape.txt", "/tmp/escape", ".git/config", "src/../../escape"))
def test_artifact_validation_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    result = GeneratedArtifactValidator().validate(tmp_path, {path: "bad"})
    assert result.valid is False
    assert any(error.startswith("unsafe_path:") for error in result.errors)


def test_run_summary_keeps_generation_separate_from_build() -> None:
    summary = ForgeXRunSummary(
        run_id="run-1",
        requested_provider="openai",
        actual_provider="openai",
        provider_type=ProviderType.API,
        generation_status=GenerationStatus.CONTENT_VERIFIED.value,
        build_status="build_failed",
    ).to_safe_dict()

    assert summary["generation_status"] == "content_verified"
    assert summary["build_status"] == "build_failed"


@pytest.mark.parametrize("task", (
    "Create an ESP32 blink project",
    "Create an Arduino blink sketch",
    "Create a PlatformIO minimal project",
    "Create a basic serial monitor project",
    "Create an ESP32 WiFi scan project",
    "Create a simple OLED test project",
    "Create a simple sensor read project",
))
def test_verified_template_matching(task: str) -> None:
    assert match_template(task) is not None


def test_custom_task_does_not_match_template() -> None:
    assert match_template("Create an advanced ESP32 WiFi dashboard") is None


@pytest.mark.asyncio
async def test_verified_template_runs_in_sandbox_and_creates_review(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    service = ProductAgentService(
        managed_sandbox_root=tmp_path / "sandboxes",
        review_service=BridgeDiffService(store=BridgeReviewStore(snapshots_path=tmp_path / "state" / "snapshots.jsonl", reviews_path=tmp_path / "state" / "reviews.jsonl")),
        provider_registry=ProductProviderRegistry(),
        enabled=True,
    )
    run = await service.start_run(
        project_id="project",
        active_workspace_root=active,
        instruction="Create an ESP32 blink project",
        provider_id="verified_template",
    )
    for _ in range(500):
        run = service.get_run(run.run_id)
        if run.status in TERMINAL_AGENT_STATUSES:
            break
        import asyncio
        await asyncio.sleep(0.01)

    assert run.status == "completed"
    assert run.review_id is not None
    assert not (active / "platformio.ini").exists()
    assert run.to_safe_dict()["summary"]["generation_source"] == "Verified template"  # type: ignore[index]
    await service.close()


@pytest.mark.asyncio
async def test_identical_verified_template_is_successful_no_op(tmp_path: Path) -> None:
    template = match_template("Create an ESP32 blink project")
    assert template is not None
    active = tmp_path / "active"
    active.mkdir()
    for relative, content in template.files:
        target = active / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    service = ProductAgentService(
        managed_sandbox_root=tmp_path / "sandboxes",
        review_service=BridgeDiffService(store=BridgeReviewStore(snapshots_path=tmp_path / "state" / "snapshots.jsonl", reviews_path=tmp_path / "state" / "reviews.jsonl")),
        provider_registry=ProductProviderRegistry(),
        enabled=True,
    )
    run = await service.start_run(
        project_id="project",
        active_workspace_root=active,
        instruction="Create an ESP32 blink project",
        provider_id="verified_template",
    )
    for _ in range(500):
        run = service.get_run(run.run_id)
        if run.status in TERMINAL_AGENT_STATUSES:
            break
        import asyncio
        await asyncio.sleep(0.01)

    assert run.status == "completed"
    assert run.review_id is None
    assert run.to_safe_dict()["summary"]["no_op_status"] is True  # type: ignore[index]
    await service.close()
