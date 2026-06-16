from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.models.firmware import BuildArtifact, FirmwareFile, FirmwareProject


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def firmware_file(path: str, content: str) -> FirmwareFile:
    return FirmwareFile(path, content, len(content.encode("utf-8")))


def test_firmware_file_validates_utf8_size_and_round_trips() -> None:
    item = firmware_file("src/main.cpp", "const char* value = \"é\";")

    assert item.size_bytes == len(item.content.encode("utf-8"))
    assert FirmwareFile.from_dict(item.to_dict()) == item
    assert FirmwareFile.from_json(item.to_json()) == item

    with pytest.raises(ValueError, match="UTF-8"):
        FirmwareFile("src/main.cpp", "é", 1)


def test_project_file_access_size_and_json_round_trip() -> None:
    main = firmware_file("src/main.cpp", "main")
    config = firmware_file("platformio.ini", "[env:test]")
    project = FirmwareProject(
        project_name="blinker",
        target_board="esp32dev",
        framework="arduino",
        generated_files=[main, config],  # type: ignore[arg-type]
        created_at=NOW,
    )

    assert project.get_file("src/main.cpp") is main
    assert project.get_file("include/missing.h") is None
    assert project.list_files() == ("src/main.cpp", "platformio.ini")
    assert project.total_size() == main.size_bytes + config.size_bytes
    assert FirmwareProject.from_json(project.to_json()) == project


def test_project_normalizes_timestamp_to_utc() -> None:
    local_time = datetime(
        2026,
        1,
        2,
        8,
        34,
        5,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    project = FirmwareProject(
        "blinker",
        "esp32dev",
        "arduino",
        (firmware_file("src/main.cpp", "main"),),
        local_time,
    )

    assert project.created_at == NOW
    assert project.to_dict()["created_at"] == "2026-01-02T03:04:05Z"


def test_project_rejects_case_insensitive_duplicate_paths() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        FirmwareProject(
            "blinker",
            "esp32dev",
            "arduino",
            (
                firmware_file("src/main.cpp", "one"),
                firmware_file("SRC/MAIN.CPP", "two"),
            ),
            NOW,
        )


def test_build_artifact_normalizes_path_and_round_trips() -> None:
    artifact = BuildArtifact(
        firmware_path=Path(".pio/build/esp32dev/firmware.bin"),  # type: ignore[arg-type]
        firmware_size=1024,
        checksum="A" * 64,
        created_at=NOW,
    )

    assert artifact.firmware_path == ".pio\\build\\esp32dev\\firmware.bin"
    assert artifact.checksum == "A" * 64
    assert BuildArtifact.from_json(artifact.to_json()) == artifact


def test_models_are_frozen() -> None:
    item = firmware_file("src/main.cpp", "main")

    with pytest.raises(FrozenInstanceError):
        item.content = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "path",
    ["../main.cpp", "/main.cpp", "src\\main.cpp", "src/CON.txt"],
)
def test_unsafe_firmware_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        firmware_file(path, "main")


def test_naive_timestamps_and_invalid_checksums_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FirmwareProject(
            "blinker",
            "esp32dev",
            "arduino",
            (firmware_file("src/main.cpp", "main"),),
            datetime(2026, 1, 1),
        )
    with pytest.raises(ValueError, match="checksum"):
        BuildArtifact("firmware.bin", 10, "", NOW)
