from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.services.project_service import (
    ProjectConflictError,
    ProjectMetadata,
    ProjectNotFoundError,
    ProjectService,
    ProjectStructureError,
)


CREATED_AT = datetime(2026, 6, 11, 8, 30, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 6, 11, 9, 0, tzinfo=timezone.utc)
MANIFEST = ".promptforge-project.json"


class RecordingCodeGenerationService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.projects: list[GeneratedProject] = []

    def validate_project(self, project: GeneratedProject) -> None:
        self.projects.append(project)
        if self.error is not None:
            raise self.error


class RecordingPlatformIOService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.paths: list[Path] = []

    def validate_project(self, path: str | Path) -> object:
        project_path = Path(path)
        self.paths.append(project_path)
        if self.error is not None:
            raise self.error
        assert (project_path / "platformio.ini").is_file()
        assert (project_path / "src" / "main.cpp").is_file()
        return object()


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def project(
    *,
    project_id: str = "project-123",
    project_name: str = "esp32-blinker",
    framework: str = "PlatformIO",
    files: tuple[GeneratedFile, ...] | None = None,
    metadata: dict[str, object] | None = None,
) -> GeneratedProject:
    return GeneratedProject(
        project_id=project_id,
        project_name=project_name,
        target_board="ESP32",
        framework=framework,
        files=files
        or (
            GeneratedFile(
                "platformio.ini",
                "[env:esp32dev]\nboard = esp32dev\n",
                "ini",
            ),
            GeneratedFile(
                "src/main.cpp",
                "void setup() {}\nvoid loop() {}\n",
                "cpp",
            ),
        ),
        created_at=CREATED_AT,
        metadata=metadata or {"task_id": "task-123"},
    )


def test_project_metadata_has_canonical_fields() -> None:
    assert [item.name for item in fields(ProjectMetadata)] == [
        "project_id",
        "project_name",
        "target_board",
        "framework",
        "project_path",
        "created_at",
        "updated_at",
        "file_count",
        "metadata",
    ]


def test_project_metadata_is_frozen_and_round_trips() -> None:
    metadata = {"labels": ["demo"]}
    value = ProjectMetadata(
        project_id="project-123",
        project_name="demo",
        target_board="ESP32",
        framework="PlatformIO",
        project_path="C:/projects/demo",
        created_at=CREATED_AT,
        updated_at=UPDATED_AT,
        file_count=2,
        metadata=metadata,
    )
    metadata["labels"].append("changed")

    assert ProjectMetadata.from_dict(value.to_dict()) == value
    assert value.metadata["labels"] == ("demo",)
    assert not hasattr(value, "__dict__")
    with pytest.raises(FrozenInstanceError):
        value.file_count = 3  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"project_id": ""},
        {"created_at": "not-a-date"},
        {"updated_at": CREATED_AT - timedelta(seconds=1)},
        {"file_count": -1},
        {"file_count": True},
        {"metadata": {"bad": object()}},
    ],
)
def test_project_metadata_rejects_invalid_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "project_id": "project-123",
        "project_name": "demo",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "project_path": "C:/projects/demo",
        "created_at": CREATED_AT,
        "updated_at": UPDATED_AT,
        "file_count": 2,
        "metadata": {},
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ProjectMetadata(**values)  # type: ignore[arg-type]


def test_public_operations_are_async() -> None:
    for method_name in (
        "create_project",
        "load_project",
        "save_project",
        "delete_project",
        "list_projects",
        "validate_project",
    ):
        assert inspect.iscoroutinefunction(getattr(ProjectService, method_name))


def test_create_project_materializes_files_and_metadata(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")

    metadata = run(service.create_project(project()))

    root = tmp_path / "projects" / "esp32-blinker"
    assert metadata.project_id == "project-123"
    assert metadata.project_path == str(root.resolve())
    assert metadata.created_at == CREATED_AT
    assert metadata.updated_at == CREATED_AT
    assert metadata.file_count == 2
    assert (root / "platformio.ini").read_text(encoding="utf-8").startswith(
        "[env:esp32dev]"
    )
    assert (root / "src" / "main.cpp").is_file()
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["files"] == [
        {"file_type": "ini", "path": "platformio.ini"},
        {"file_type": "cpp", "path": "src/main.cpp"},
    ]


def test_load_project_uses_disk_files_as_source_of_truth(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))
    main = tmp_path / "projects" / "esp32-blinker" / "src" / "main.cpp"
    main.write_text("void setup() { changed(); }", encoding="utf-8")

    loaded = run(service.load_project("esp32-blinker"))

    assert loaded.project_id == "project-123"
    assert loaded.get_file("src/main.cpp").content == (
        "void setup() { changed(); }"
    )
    assert loaded.metadata == {"task_id": "task-123"}


def test_save_project_atomically_replaces_files_and_removes_stale_files(
    tmp_path: Path,
) -> None:
    service = ProjectService(
        tmp_path / "projects",
        clock=lambda: UPDATED_AT,
    )
    run(service.create_project(project()))
    replacement = project(
        files=(GeneratedFile("main.ino", "void setup() {}", "ino"),),
        framework="Arduino",
    )

    metadata = run(service.save_project(replacement))
    loaded = run(service.load_project("esp32-blinker"))

    root = tmp_path / "projects" / "esp32-blinker"
    assert metadata.updated_at == UPDATED_AT
    assert metadata.file_count == 1
    assert loaded.list_files() == ("main.ino",)
    assert not (root / "platformio.ini").exists()
    assert not (root / "src").exists()
    assert not (tmp_path / "projects" / ".esp32-blinker.backup").exists()


def test_create_and_save_enforce_project_ownership(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))

    with pytest.raises(ProjectConflictError, match="already exists"):
        run(service.create_project(project(project_id="project-other")))
    with pytest.raises(ProjectConflictError, match="does not match"):
        run(service.save_project(project(project_id="project-other")))
    with pytest.raises(ProjectNotFoundError):
        run(service.save_project(project(project_name="missing")))


def test_save_preserves_timestamp_history(tmp_path: Path) -> None:
    clock_values = iter(
        [UPDATED_AT, CREATED_AT + timedelta(minutes=10)]
    )
    service = ProjectService(
        tmp_path / "projects",
        clock=lambda: next(clock_values),
    )
    generated = project()
    run(service.create_project(generated))

    first = run(service.save_project(generated))
    second = run(service.save_project(generated))

    assert first.updated_at == UPDATED_AT
    assert second.updated_at == UPDATED_AT
    changed_creation = GeneratedProject(
        project_id=generated.project_id,
        project_name=generated.project_name,
        target_board=generated.target_board,
        framework=generated.framework,
        files=generated.files,
        created_at=CREATED_AT + timedelta(seconds=1),
        metadata=generated.metadata,
    )
    with pytest.raises(ProjectConflictError, match="created_at"):
        run(service.save_project(changed_creation))


def test_duplicate_project_id_is_rejected_across_names(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))

    with pytest.raises(ProjectConflictError, match="project_id"):
        run(service.create_project(project(project_name="second-project")))


def test_delete_project_is_idempotent(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))

    assert run(service.delete_project("esp32-blinker")) is True
    assert run(service.delete_project("esp32-blinker")) is False
    with pytest.raises(ProjectNotFoundError):
        run(service.load_project("esp32-blinker"))


def test_list_projects_is_stable_and_ignores_unmanaged_directories(
    tmp_path: Path,
) -> None:
    service = ProjectService(tmp_path / "projects")
    run(
        service.create_project(
            project(project_id="project-z", project_name="zeta")
        )
    )
    run(
        service.create_project(
            project(project_id="project-a", project_name="alpha")
        )
    )
    unmanaged = tmp_path / "projects" / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / "readme.txt").write_text("ignore", encoding="utf-8")

    projects = run(service.list_projects())

    assert tuple(item.project_name for item in projects) == ("alpha", "zeta")


def test_manifest_output_is_deterministic(tmp_path: Path) -> None:
    first = ProjectService(tmp_path / "first")
    second = ProjectService(tmp_path / "second")
    generated = project(metadata={"z": 1, "a": [2, 3]})

    run(first.create_project(generated))
    run(second.create_project(generated))

    first_manifest = (
        tmp_path / "first" / "esp32-blinker" / MANIFEST
    ).read_bytes()
    second_manifest = (
        tmp_path / "second" / "esp32-blinker" / MANIFEST
    ).read_bytes()
    assert first_manifest == second_manifest


@pytest.mark.parametrize(
    "name",
    ["", "../escape", "Bad Name", "UPPER", "-prefix", "suffix-"],
)
def test_operations_reject_unsafe_project_names(
    tmp_path: Path,
    name: str,
) -> None:
    service = ProjectService(tmp_path / "projects")
    with pytest.raises(ValueError, match="project_name"):
        run(service.load_project(name))
    with pytest.raises(ValueError, match="project_name"):
        run(service.delete_project(name))


def test_create_rejects_unsafe_or_reserved_structure(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    unsafe_name = project(project_name="Bad Name")
    reserved = project(
        files=(GeneratedFile(MANIFEST, "collision", "json"),)
    )

    with pytest.raises(ValueError, match="project_name"):
        run(service.create_project(unsafe_name))
    with pytest.raises(ProjectStructureError, match="reserved"):
        run(service.create_project(reserved))


def test_load_rejects_missing_file_and_invalid_manifest(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))
    root = tmp_path / "projects" / "esp32-blinker"
    (root / "src" / "main.cpp").unlink()

    with pytest.raises(ProjectStructureError, match="missing"):
        run(service.load_project("esp32-blinker"))

    (root / MANIFEST).write_text("not json", encoding="utf-8")
    with pytest.raises(ProjectStructureError, match="manifest is invalid"):
        run(service.load_project("esp32-blinker"))


def test_load_rejects_manifest_name_mismatch(tmp_path: Path) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))
    manifest_path = tmp_path / "projects" / "esp32-blinker" / MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["project_name"] = "other"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ProjectStructureError, match="does not match"):
        run(service.load_project("esp32-blinker"))


def test_validate_project_integrates_code_generation_service(
    tmp_path: Path,
) -> None:
    validator = RecordingCodeGenerationService()
    service = ProjectService(
        tmp_path / "projects",
        code_generation_service=validator,  # type: ignore[arg-type]
    )
    generated = project()

    assert run(service.validate_project(generated)) is None
    run(service.create_project(generated))
    assert run(service.validate_project("esp32-blinker")) is None

    assert validator.projects[0] is generated
    assert validator.projects[-1].project_id == generated.project_id


def test_code_generation_validation_failure_prevents_create(
    tmp_path: Path,
) -> None:
    validator = RecordingCodeGenerationService(ValueError("invalid firmware"))
    service = ProjectService(
        tmp_path / "projects",
        code_generation_service=validator,  # type: ignore[arg-type]
    )

    with pytest.raises(ProjectStructureError, match="invalid firmware"):
        run(service.create_project(project()))
    assert not (tmp_path / "projects" / "esp32-blinker").exists()


def test_platformio_validation_inspects_staged_and_persisted_projects(
    tmp_path: Path,
) -> None:
    validator = RecordingPlatformIOService()
    service = ProjectService(
        tmp_path / "projects",
        platformio_service=validator,  # type: ignore[arg-type]
    )

    run(service.create_project(project()))
    run(service.validate_project("esp32-blinker"))

    assert len(validator.paths) == 2
    assert validator.paths[0].name.startswith(".promptforge-")
    assert validator.paths[1].name == "esp32-blinker"


def test_platformio_failure_does_not_publish_staged_project(
    tmp_path: Path,
) -> None:
    validator = RecordingPlatformIOService(ValueError("invalid PlatformIO"))
    service = ProjectService(
        tmp_path / "projects",
        platformio_service=validator,  # type: ignore[arg-type]
    )

    with pytest.raises(ProjectStructureError, match="invalid PlatformIO"):
        run(service.create_project(project()))
    assert not (tmp_path / "projects" / "esp32-blinker").exists()
    assert list((tmp_path / "projects").iterdir()) == []


def test_non_platformio_project_skips_platformio_validation(
    tmp_path: Path,
) -> None:
    validator = RecordingPlatformIOService()
    service = ProjectService(
        tmp_path / "projects",
        platformio_service=validator,  # type: ignore[arg-type]
    )

    run(
        service.create_project(
            project(
                framework="Arduino",
                files=(GeneratedFile("main.ino", "void setup() {}", "ino"),),
            )
        )
    )

    assert validator.paths == []


def test_failed_save_preserves_the_existing_project(tmp_path: Path) -> None:
    validator = RecordingPlatformIOService()
    service = ProjectService(
        tmp_path / "projects",
        platformio_service=validator,  # type: ignore[arg-type]
    )
    original = project()
    run(service.create_project(original))
    validator.error = ValueError("replacement rejected")
    replacement = project(
        files=(
            GeneratedFile("platformio.ini", "[env:new]\nboard = new", "ini"),
            GeneratedFile("src/main.cpp", "changed", "cpp"),
        )
    )

    with pytest.raises(ProjectStructureError, match="replacement rejected"):
        run(service.save_project(replacement))

    loaded = run(service.load_project("esp32-blinker"))
    assert loaded == original


def test_symlink_project_is_rejected_when_supported(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = projects_root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    service = ProjectService(projects_root)
    with pytest.raises(ProjectStructureError, match="symlink"):
        run(service.delete_project("linked"))


def test_symlink_file_component_is_rejected_when_supported(
    tmp_path: Path,
) -> None:
    service = ProjectService(tmp_path / "projects")
    run(service.create_project(project()))
    root = tmp_path / "projects" / "esp32-blinker"
    source = root / "src"
    replacement = root / "source-real"
    source.rename(replacement)
    try:
        source.symlink_to(replacement, target_is_directory=True)
    except OSError:
        replacement.rename(source)
        pytest.skip("directory symlinks are not available")

    with pytest.raises(ProjectStructureError, match="symlink"):
        run(service.load_project("esp32-blinker"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"projects_root": ""},
        {"projects_root": object()},
        {"projects_root": "projects", "clock": object()},
        {"projects_root": "projects", "code_generation_service": object()},
        {"projects_root": "projects", "platformio_service": object()},
    ],
)
def test_service_rejects_invalid_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ProjectService(**kwargs)  # type: ignore[arg-type]


def test_projects_root_cannot_be_a_file(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.write_text("not a directory", encoding="utf-8")
    service = ProjectService(root)

    with pytest.raises(ProjectStructureError, match="directory"):
        run(service.list_projects())
