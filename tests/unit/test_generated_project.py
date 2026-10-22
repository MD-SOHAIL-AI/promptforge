from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent.planner import Framework, TargetBoard
from backend.contracts.generated_project import GeneratedFile, GeneratedProject


CREATED_AT = datetime(2026, 6, 11, 8, 30, tzinfo=timezone.utc)


def generated_files() -> tuple[GeneratedFile, ...]:
    return (
        GeneratedFile(
            path="platformio.ini",
            content="[env:esp32dev]\nboard = esp32dev\n",
            file_type="ini",
        ),
        GeneratedFile(
            path="src/main.cpp",
            content="void setup() {}\nvoid loop() {}\n",
            file_type="cpp",
        ),
    )


def make_project(**overrides: object) -> GeneratedProject:
    values: dict[str, object] = {
        "project_id": "project-123",
        "project_name": "esp32-blinker",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "files": generated_files(),
        "created_at": CREATED_AT,
        "metadata": {"task_id": "task-123", "labels": ["demo"]},
    }
    values.update(overrides)
    return GeneratedProject(**values)  # type: ignore[arg-type]


def test_generated_file_has_canonical_fields() -> None:
    assert [item.name for item in fields(GeneratedFile)] == [
        "path",
        "content",
        "file_type",
    ]


def test_generated_file_is_frozen_slotted_and_serializable() -> None:
    generated_file = GeneratedFile("src/main.c", "int main(void) {}", "c")

    assert GeneratedFile.from_dict(generated_file.to_dict()) == generated_file
    assert not hasattr(generated_file, "__dict__")
    with pytest.raises(FrozenInstanceError):
        generated_file.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("path", ""),
        ("path", "   "),
        ("path", "bad\x00.c"),
        ("content", "bad\x00content"),
        ("file_type", ""),
        ("file_type", None),
    ],
)
def test_generated_file_rejects_invalid_fields(
    field_name: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "path": "main.c",
        "content": "content",
        "file_type": "c",
    }
    values[field_name] = value

    with pytest.raises(ValueError, match=field_name):
        GeneratedFile(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("content", ["", "   "])
def test_generated_file_allows_empty_text_content(content: str) -> None:
    assert GeneratedFile(path="main.c", content=content).content == content


@pytest.mark.parametrize(
    "path",
    [
        "/main.c",
        "../main.c",
        "src/../main.c",
        "src\\main.c",
        "src//main.c",
        "src/bad?.c",
        "CON.txt",
    ],
)
def test_generated_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        GeneratedFile(path, "content", "c")


def test_generated_file_from_dict_requires_exact_schema() -> None:
    with pytest.raises(ValueError, match="missing.*file_type"):
        GeneratedFile.from_dict({"path": "main.c", "content": "content"})
    with pytest.raises(ValueError, match="unknown.*size"):
        GeneratedFile.from_dict(
            {
                "path": "main.c",
                "content": "content",
                "file_type": "c",
                "size": 7,
            }
        )


def test_generated_project_has_canonical_fields() -> None:
    assert [item.name for item in fields(GeneratedProject)] == [
        "project_id",
        "project_name",
        "target_board",
        "framework",
        "files",
        "created_at",
        "metadata",
    ]


def test_generated_project_is_frozen_slotted_and_defensively_copied() -> None:
    files = list(generated_files())
    metadata = {"nested": {"pins": [2]}}
    project = make_project(files=files, metadata=metadata)
    files.pop()
    metadata["nested"]["pins"].append(4)  # type: ignore[index,union-attr]

    assert len(project.files) == 2
    assert project.metadata["nested"]["pins"] == (2,)  # type: ignore[index]
    assert not hasattr(project, "__dict__")
    with pytest.raises(FrozenInstanceError):
        project.project_name = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        project.metadata["new"] = True  # type: ignore[index]


def test_project_accepts_string_enums_and_serializes_their_values() -> None:
    project = make_project(
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
    )

    serialized = project.to_dict()

    assert serialized["target_board"] == "ESP32"
    assert serialized["framework"] == "PlatformIO"
    json.dumps(serialized)


def test_created_at_is_normalized_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    project = make_project(
        created_at=datetime(2026, 6, 11, 14, 0, tzinfo=offset)
    )

    assert project.created_at == CREATED_AT
    assert project.to_dict()["created_at"] == "2026-06-11T08:30:00Z"


def test_project_round_trips_and_returns_fresh_serialized_data() -> None:
    project = make_project()

    serialized = project.to_dict()
    restored = GeneratedProject.from_dict(serialized)
    serialized["files"][0]["content"] = "changed"  # type: ignore[index]
    serialized["metadata"]["labels"].append("changed")  # type: ignore[index]

    assert restored == project
    assert project.files[0].content.startswith("[env:")
    assert project.metadata["labels"] == ("demo",)


def test_get_file_and_list_files_preserve_project_order() -> None:
    project = make_project()

    assert project.list_files() == ("platformio.ini", "src/main.cpp")
    assert project.get_file("src/main.cpp") is project.files[1]
    assert project.get_file("src/missing.cpp") is None
    with pytest.raises(ValueError, match="path"):
        project.get_file("")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("project_id", ""),
        ("project_name", " "),
        ("target_board", None),
        ("framework", "bad\x00framework"),
    ],
)
def test_project_rejects_invalid_scalar_fields(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_project(**{field_name: value})


def test_project_rejects_invalid_files() -> None:
    with pytest.raises(ValueError, match="files must be a sequence"):
        make_project(files="main.c")
    with pytest.raises(ValueError, match="at least one"):
        make_project(files=())
    with pytest.raises(ValueError, match="GeneratedFile"):
        make_project(files=(object(),))
    with pytest.raises(ValueError, match="duplicate paths"):
        make_project(
            files=(
                GeneratedFile("src/main.cpp", "one", "cpp"),
                GeneratedFile("SRC/Main.cpp", "two", "cpp"),
            )
        )


@pytest.mark.parametrize(
    "created_at",
    ["2026-06-11T08:30:00Z", datetime(2026, 6, 11, 8, 30)],
)
def test_project_rejects_invalid_created_at(created_at: object) -> None:
    with pytest.raises(ValueError, match="created_at"):
        make_project(created_at=created_at)


@pytest.mark.parametrize(
    "metadata",
    [[], {"": "value"}, {"object": object()}, {"nan": float("nan")}],
)
def test_project_rejects_invalid_metadata(metadata: object) -> None:
    with pytest.raises(ValueError, match="metadata"):
        make_project(metadata=metadata)


def test_project_rejects_cyclic_metadata() -> None:
    metadata: dict[str, object] = {}
    metadata["self"] = metadata

    with pytest.raises(ValueError, match="cyclic"):
        make_project(metadata=metadata)


def test_project_from_dict_requires_exact_schema_and_valid_timestamp() -> None:
    serialized = make_project().to_dict()
    missing = dict(serialized)
    missing.pop("project_id")
    unknown = {**serialized, "task_id": "task-123"}
    invalid_time = {**serialized, "created_at": "not-a-time"}

    with pytest.raises(ValueError, match="missing.*project_id"):
        GeneratedProject.from_dict(missing)
    with pytest.raises(ValueError, match="unknown.*task_id"):
        GeneratedProject.from_dict(unknown)
    with pytest.raises(ValueError, match="valid ISO 8601"):
        GeneratedProject.from_dict(invalid_time)
    with pytest.raises(ValueError, match="mapping"):
        GeneratedProject.from_dict([])  # type: ignore[arg-type]
