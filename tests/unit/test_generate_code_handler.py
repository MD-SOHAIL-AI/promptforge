from __future__ import annotations

import asyncio
import logging
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.agent.coordinator import Coordinator, ExecutionStatus, StepResult
from backend.contracts.execution_context import ExecutionContext
from backend.contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    TaskType,
)
from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.services.code_generation_service import (
    CodeGenerationRequest,
    GeneratedOutputError,
    ProjectValidationError,
)
from backend.services.project_service import ProjectMetadata
from backend.services.project_service import ProjectService
from backend.workflow.generate_code_handler import (
    GenerateCodeHandler,
    GenerateCodeResult,
    GenerateCodeStatus,
)


CREATED_AT = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def plan(**overrides: object) -> ExecutionPlan:
    values: dict[str, object] = {
        "task_id": "task-123",
        "task_type": TaskType.FIRMWARE_GENERATION,
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "requirements": ("LED output",),
        "execution_steps": (
            ExecutionStep.GENERATE_CODE,
            ExecutionStep.BUILD_FIRMWARE,
        ),
        "confidence": 0.9,
        "metadata": {},
    }
    values.update(overrides)
    return ExecutionPlan(**values)  # type: ignore[arg-type]


def context(**overrides: object) -> ExecutionContext:
    values: dict[str, object] = {
        "task_id": "task-123",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "metadata": {"request_id": "request-7"},
    }
    values.update(overrides)
    return ExecutionContext(**values)  # type: ignore[arg-type]


def project(**overrides: object) -> GeneratedProject:
    values: dict[str, object] = {
        "project_id": "project-123",
        "project_name": "blink",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "files": (
            GeneratedFile(
                "platformio.ini",
                "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\n",
            ),
            GeneratedFile(
                "src/main.cpp",
                "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
            ),
        ),
        "created_at": CREATED_AT,
        "metadata": {"task_id": "task-123"},
    }
    values.update(overrides)
    return GeneratedProject(**values)  # type: ignore[arg-type]


def persisted(**overrides: object) -> ProjectMetadata:
    values: dict[str, object] = {
        "project_id": "project-123",
        "project_name": "blink",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "project_path": "C:/workspace/projects/blink",
        "created_at": CREATED_AT,
        "updated_at": CREATED_AT,
        "file_count": 2,
        "metadata": {"task_id": "task-123"},
    }
    values.update(overrides)
    return ProjectMetadata(**values)  # type: ignore[arg-type]


class FakeGenerationService:
    def __init__(
        self,
        output: object | None = None,
        *,
        generate_error: Exception | None = None,
        validation_error: Exception | None = None,
    ) -> None:
        self.output = project() if output is None else output
        self.generate_error = generate_error
        self.validation_error = validation_error
        self.requests: list[CodeGenerationRequest] = []
        self.validated: list[GeneratedProject] = []

    async def generate_project(
        self,
        request: CodeGenerationRequest,
    ) -> object:
        self.requests.append(request)
        if self.generate_error is not None:
            raise self.generate_error
        return self.output

    def validate_project(self, generated: GeneratedProject) -> None:
        self.validated.append(generated)
        if self.validation_error is not None:
            raise self.validation_error


class FakeProjectService:
    def __init__(
        self,
        output: object | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.output = persisted() if output is None else output
        self.error = error
        self.projects: list[GeneratedProject] = []

    async def create_project(self, generated: GeneratedProject) -> object:
        self.projects.append(generated)
        if self.error is not None:
            raise self.error
        return self.output


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def clock(*values: float) -> Any:
    sequence = iter(values)
    return lambda: next(sequence)


def test_successful_generation_persistence_and_context_update(
    caplog: pytest.LogCaptureFixture,
) -> None:
    generation = FakeGenerationService(
        project(metadata={"task_id": "task-123", "warnings": ["pin inferred"]})
    )
    projects = FakeProjectService()
    handler = GenerateCodeHandler(
        generation,
        projects,
        context(),
        clock=clock(10.0, 10.125),
    )

    with caplog.at_level(logging.INFO):
        result = run(handler.execute(plan()))

    assert result.status is GenerateCodeStatus.SUCCESS
    assert result.success is True
    assert result.generated_project is generation.output
    assert result.project_path == "C:/workspace/projects/blink"
    assert result.generation_time_ms == 125
    assert result.warnings == ("pin inferred",)
    assert len(generation.requests) == 1
    assert generation.requests[0].plan == plan()
    assert generation.requests[0].context == context()
    assert generation.validated == [generation.output]
    assert projects.projects == [generation.output]

    updated = result.execution_context
    assert updated is not None
    assert updated.project_path == "C:/workspace/projects/blink"
    assert updated.target_board == "ESP32"
    assert updated.framework == "PlatformIO"
    assert updated.metadata["request_id"] == "request-7"
    assert updated.metadata["workflow"]["generation"] == {
        "project_id": "project-123",
        "project_name": "blink",
        "file_count": 2,
    }
    assert result.metadata["project_id"] == "project-123"
    assert "generation_start" in caplog.text
    assert "generation_completion" in caplog.text
    assert "persistence_start" in caplog.text
    assert "persistence_completion" in caplog.text


def test_result_is_frozen_slotted_and_metadata_is_immutable() -> None:
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(),
            FakeProjectService(),
            context(),
        ).execute(plan())
    )

    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.project_path = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.metadata["new"] = True  # type: ignore[index]


def test_successful_handler_persists_with_real_project_service(
    tmp_path: Path,
) -> None:
    generated = project()
    handler = GenerateCodeHandler(
        FakeGenerationService(generated),  # type: ignore[arg-type]
        ProjectService(tmp_path / "projects"),
        context(),
    )

    result = run(handler.execute(plan()))

    expected = (tmp_path / "projects" / "blink").resolve()
    assert result.status is GenerateCodeStatus.SUCCESS
    assert result.project_path == str(expected)
    assert (expected / "platformio.ini").is_file()
    assert (expected / "src" / "main.cpp").is_file()
    summary = result.metadata["artifact_summary"]
    assert summary["workspace_root"] == str(expected)
    assert summary["required_files_present"] is True
    assert summary["generation_satisfied_prompt"] is True
    assert set(summary["created_files"]) >= {"platformio.ini", "src/main.cpp"}


def test_generation_into_empty_folder_reports_artifact_summary(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    active_workspace = {
        "id": "empty-workspace",
        "rootPath": str(workspace),
        "generation_mode": "generate_into_open_folder",
    }
    handler = GenerateCodeHandler(
        FakeGenerationService(project()),
        FakeProjectService(),
        context(metadata={"active_workspace": active_workspace, "prompt": "Blink LED on ESP32"}),
    )

    result = run(handler.execute(plan()))

    assert result.status is GenerateCodeStatus.SUCCESS
    summary = result.metadata["artifact_summary"]
    assert summary["workspace_root"] == str(workspace.resolve())
    assert summary["platformio_ini_present"] is True
    assert summary["main_cpp_present"] is True
    assert tuple(summary["created_files"]) == (
        ".promptforge-project.json",
        "platformio.ini",
        "src/main.cpp",
    )
    assert tuple(summary["updated_files"]) == ()


def test_generation_into_open_folder_accepts_verified_unchanged_files(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "stale-workspace"
    (workspace / "src").mkdir(parents=True)
    generated = project()
    for file in generated.files:
        target = workspace / Path(*file.path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.content, encoding="utf-8")
    active_workspace = {
        "id": "stale-workspace",
        "rootPath": str(workspace),
        "generation_mode": "generate_into_open_folder",
    }
    handler = GenerateCodeHandler(
        FakeGenerationService(generated),
        FakeProjectService(),
        context(metadata={"active_workspace": active_workspace, "prompt": "Blink LED on ESP32"}),
    )

    result = run(handler.execute(plan()))

    assert result.status is GenerateCodeStatus.SUCCESS
    assert result.success is True
    summary = result.metadata["artifact_summary"]
    assert summary["content_verified"] is True
    assert summary["no_op"] is True
    assert summary["generation_satisfied_prompt"] is True
    assert set(summary["unchanged_files"]) >= {"platformio.ini", "src/main.cpp"}


def test_generation_into_open_folder_overwrites_mismatched_workspace_content(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "stale-workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "platformio.ini").write_text("[env:stale]\n", encoding="utf-8")
    (workspace / "src" / "main.cpp").write_text("// stale\n", encoding="utf-8")
    active_workspace = {
        "id": "stale-workspace",
        "rootPath": str(workspace),
        "generation_mode": "generate_into_open_folder",
    }
    handler = GenerateCodeHandler(
        FakeGenerationService(project()),
        FakeProjectService(),
        context(metadata={"active_workspace": active_workspace, "prompt": "Blink LED on ESP32"}),
    )

    result = run(handler.execute(plan()))

    assert result.status is GenerateCodeStatus.SUCCESS
    assert result.success is True
    assert (workspace / "platformio.ini").read_text(encoding="utf-8") == next(
        item.content for item in project().files if item.path == "platformio.ini"
    )
    assert (workspace / "src" / "main.cpp").read_text(encoding="utf-8") == next(
        item.content for item in project().files if item.path == "src/main.cpp"
    )
    summary = result.metadata["artifact_summary"]
    assert summary["content_verified"] is True
    assert set(summary["updated_files"]) >= {"platformio.ini", "src/main.cpp"}


def test_modifying_existing_project_preserves_platformio_and_rejects_mismatch(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "existing-project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "platformio.ini").write_text("[env:custom]\n", encoding="utf-8")
    (workspace / "src" / "main.cpp").write_text("// existing\n", encoding="utf-8")
    active_workspace = {
        "id": "existing-project",
        "rootPath": str(workspace),
        "generation_mode": "modify_existing_project",
    }
    handler = GenerateCodeHandler(
        FakeGenerationService(project()),
        FakeProjectService(),
        context(metadata={"active_workspace": active_workspace, "prompt": "Blink LED on ESP32"}),
    )

    result = run(handler.execute(plan()))

    assert result.status is GenerateCodeStatus.PERSISTENCE_FAILED
    assert result.success is False
    assert "do not match the generated project content" in result.message
    assert (workspace / "platformio.ini").read_text(encoding="utf-8") == "[env:custom]\n"


@pytest.mark.parametrize(
    "invalid_plan",
    [
        object(),
        plan(task_type=TaskType.FLASH_ONLY),
        plan(execution_steps=(ExecutionStep.BUILD_FIRMWARE,)),
        plan(target_board="UNKNOWN"),
        plan(framework="UNKNOWN"),
    ],
)
def test_invalid_execution_plans_are_structured_failures(
    invalid_plan: object,
    caplog: pytest.LogCaptureFixture,
) -> None:
    generation = FakeGenerationService()
    handler = GenerateCodeHandler(generation, FakeProjectService(), context())

    with caplog.at_level(logging.WARNING):
        result = run(handler.execute(invalid_plan))  # type: ignore[arg-type]

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert result.success is False
    assert result.generated_project is None
    assert result.project_path is None
    assert result.metadata["phase"] == "request_validation"
    assert generation.requests == []
    assert "validation_failed" in caplog.text


def test_plan_and_context_identity_must_match() -> None:
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(),
            FakeProjectService(),
            context(task_id="task-other"),
        ).execute(plan())
    )

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert "context.task_id" in result.message


def test_invalid_context_workflow_metadata_fails_before_generation() -> None:
    generation = FakeGenerationService()
    result = run(
        GenerateCodeHandler(
            generation,
            FakeProjectService(),
            context(metadata={"workflow": "invalid"}),
        ).execute(plan())
    )

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert generation.requests == []


def test_invalid_completed_results_are_structured() -> None:
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(),
            FakeProjectService(),
            context(),
        ).execute(plan(), completed=(object(),))  # type: ignore[arg-type]
    )

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert result.metadata["phase"] == "request_validation"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("provider unavailable"), GenerateCodeStatus.GENERATION_FAILED),
        (
            GeneratedOutputError("malformed response"),
            GenerateCodeStatus.GENERATION_FAILED,
        ),
        (
            ProjectValidationError("invalid generated project"),
            GenerateCodeStatus.VALIDATION_FAILED,
        ),
    ],
)
def test_generation_exceptions_are_classified(
    error: Exception,
    expected: GenerateCodeStatus,
) -> None:
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(generate_error=error),
            FakeProjectService(),
            context(),
        ).execute(plan())
    )

    assert result.status is expected
    assert result.message == str(error)
    assert result.metadata["error"]["type"] == type(error).__name__


@pytest.mark.parametrize(
    "generated",
    [
        object(),
        project(files=(GeneratedFile("src/main.cpp", "void setup() {}"),)),
        project(metadata={"task_id": "task-other"}),
        project(target_board="STM32"),
        project(framework="Arduino"),
        project(metadata={"task_id": "task-123", "warnings": "warning"}),
    ],
)
def test_malformed_generated_projects_fail_before_persistence(
    generated: object,
) -> None:
    projects = FakeProjectService()
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(generated),
            projects,
            context(),
        ).execute(plan())
    )

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert result.metadata["phase"] == "project_validation"
    assert projects.projects == []


def test_service_output_validation_failure_is_structured() -> None:
    projects = FakeProjectService()
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(
                validation_error=ProjectValidationError("missing setup")
            ),
            projects,
            context(),
        ).execute(plan())
    )

    assert result.status is GenerateCodeStatus.VALIDATION_FAILED
    assert result.message == "missing setup"
    assert projects.projects == []


@pytest.mark.parametrize(
    "project_service",
    [
        FakeProjectService(error=OSError("disk full")),
        FakeProjectService(output=object()),
        FakeProjectService(output=persisted(project_id="project-other")),
        FakeProjectService(output=persisted(file_count=1)),
    ],
)
def test_persistence_failures_are_structured(
    project_service: FakeProjectService,
) -> None:
    result = run(
        GenerateCodeHandler(
            FakeGenerationService(),
            project_service,
            context(),
        ).execute(plan())
    )

    assert result.status is GenerateCodeStatus.PERSISTENCE_FAILED
    assert result.metadata["phase"] == "persistence"
    assert result.generated_project is None
    assert result.project_path is None


def test_arduino_context_update_is_framework_neutral() -> None:
    generated = project(
        target_board="Arduino Uno",
        framework="Arduino",
        files=(
            GeneratedFile(
                "blink.ino",
                "void setup() {}\nvoid loop() {}\n",
            ),
        ),
    )
    metadata = persisted(
        target_board="Arduino Uno",
        framework="Arduino",
        file_count=1,
    )
    arduino_plan = plan(
        target_board="Arduino Uno",
        framework="Arduino",
    )
    arduino_context = context(
        target_board="Arduino Uno",
        framework="Arduino",
    )

    result = run(
        GenerateCodeHandler(
            FakeGenerationService(generated),
            FakeProjectService(metadata),
            arduino_context,
        ).execute(arduino_plan)
    )

    assert result.status is GenerateCodeStatus.SUCCESS
    assert result.execution_context.framework == "Arduino"


def test_coordinator_treats_handler_failure_as_failed_step() -> None:
    handler = GenerateCodeHandler(
        FakeGenerationService(generate_error=RuntimeError("generation down")),
        FakeProjectService(),
        context(),
    )
    coordinator = Coordinator(
        step_handlers={ExecutionStep.GENERATE_CODE: handler},
        debug_on_failure=False,
    )

    outcome = run(coordinator.execute(plan(execution_steps=(ExecutionStep.GENERATE_CODE,))))

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.failures[0].category == "GENERATION_FAILED"
    assert outcome.failures[0].message == "generation down"


def test_coordinator_exposes_updated_context_to_downstream_resolver() -> None:
    handler = GenerateCodeHandler(
        FakeGenerationService(),
        FakeProjectService(),
        context(),
    )
    captured: list[ExecutionContext] = []

    def build_invocation(
        execution_plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> Any:
        result = completed[0].result
        assert isinstance(result, GenerateCodeResult)
        captured.append(result.execution_context)
        from backend.agent.coordinator import ToolInvocation

        return ToolInvocation()

    coordinator = Coordinator(
        step_handlers={ExecutionStep.GENERATE_CODE: handler},
        tool_executor=lambda name: True,
    )
    outcome = run(
        coordinator.execute(
            plan(),
            invocations={ExecutionStep.BUILD_FIRMWARE: build_invocation},
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert captured[0].project_path == "C:/workspace/projects/blink"


def test_external_cancellation_propagates_to_coordinator() -> None:
    class CancellingGenerationService(FakeGenerationService):
        async def generate_project(self, request: CodeGenerationRequest) -> object:
            raise asyncio.CancelledError

    handler = GenerateCodeHandler(
        CancellingGenerationService(),
        FakeProjectService(),
        context(),
    )
    coordinator = Coordinator(
        step_handlers={ExecutionStep.GENERATE_CODE: handler},
        debug_on_failure=False,
    )

    outcome = run(
        coordinator.execute(
            plan(execution_steps=(ExecutionStep.GENERATE_CODE,))
        )
    )

    assert outcome.status is ExecutionStatus.CANCELLED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code_generation_service": object()},
        {"project_service": object()},
        {"context": object()},
        {"clock": object()},
    ],
)
def test_constructor_rejects_invalid_dependencies(
    kwargs: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "code_generation_service": FakeGenerationService(),
        "project_service": FakeProjectService(),
        "context": context(),
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        GenerateCodeHandler(**values)  # type: ignore[arg-type]


def test_result_rejects_inconsistent_success_and_failure_shapes() -> None:
    with pytest.raises(ValueError, match="requires project"):
        GenerateCodeResult(
            status=GenerateCodeStatus.SUCCESS,
            generated_project=None,
            project_path=None,
            generation_time_ms=0,
        )
    with pytest.raises(ValueError, match="failed result"):
        GenerateCodeResult(
            status=GenerateCodeStatus.GENERATION_FAILED,
            generated_project=project(),
            project_path="path",
            generation_time_ms=0,
        )
