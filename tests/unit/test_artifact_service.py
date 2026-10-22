from __future__ import annotations

import asyncio
import inspect
import json
import zipfile
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

from backend.services.artifact_service import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactMetadata,
    ArtifactNotFoundError,
    ArtifactService,
    ArtifactType,
)


CREATED_AT = datetime(2026, 6, 11, 8, 30, tzinfo=timezone.utc)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def service(root: Path, created_at: datetime = CREATED_AT) -> ArtifactService:
    return ArtifactService(root, clock=lambda: created_at)


def test_artifact_types_are_canonical() -> None:
    assert [item.value for item in ArtifactType] == [
        "FIRMWARE_BINARY",
        "BUILD_LOG",
        "SERIAL_LOG",
        "SIMULATION_OUTPUT",
        "GENERATED_SOURCE_ARCHIVE",
    ]


def test_artifact_metadata_has_canonical_fields() -> None:
    assert [item.name for item in fields(ArtifactMetadata)] == [
        "artifact_id",
        "artifact_type",
        "name",
        "artifact_path",
        "content_type",
        "size_bytes",
        "sha256",
        "created_at",
        "metadata",
    ]


def test_artifact_metadata_is_frozen_slotted_and_round_trips() -> None:
    source = {"labels": ["release"]}
    metadata = ArtifactMetadata(
        artifact_id="artifact-123",
        artifact_type=ArtifactType.FIRMWARE_BINARY,
        name="firmware.bin",
        artifact_path="C:/artifacts/artifact-123/payload",
        content_type="application/octet-stream",
        size_bytes=8,
        sha256="a" * 64,
        created_at=CREATED_AT,
        metadata=source,
    )
    source["labels"].append("changed")

    assert ArtifactMetadata.from_dict(metadata.to_dict()) == metadata
    assert metadata.metadata["labels"] == ("release",)
    assert not hasattr(metadata, "__dict__")
    with pytest.raises(FrozenInstanceError):
        metadata.size_bytes = 0  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"artifact_id": "../bad"},
        {"artifact_type": "BUILD_LOG"},
        {"name": "../build.log"},
        {"artifact_path": ""},
        {"content_type": ""},
        {"size_bytes": -1},
        {"size_bytes": True},
        {"sha256": "bad"},
        {"created_at": datetime(2026, 6, 11)},
        {"metadata": {"bad": object()}},
    ],
)
def test_artifact_metadata_rejects_invalid_values(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "artifact_id": "artifact-123",
        "artifact_type": ArtifactType.BUILD_LOG,
        "name": "build.log",
        "artifact_path": "C:/artifacts/artifact-123/payload",
        "content_type": "text/plain",
        "size_bytes": 3,
        "sha256": "a" * 64,
        "created_at": CREATED_AT,
        "metadata": {},
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ArtifactMetadata(**values)  # type: ignore[arg-type]


def test_public_operations_are_async() -> None:
    for method_name in (
        "save_artifact",
        "load_artifact",
        "delete_artifact",
        "list_artifacts",
        "archive_artifacts",
    ):
        assert inspect.iscoroutinefunction(getattr(ArtifactService, method_name))


@pytest.mark.parametrize(
    ("artifact_type", "name", "content"),
    [
        (ArtifactType.FIRMWARE_BINARY, "firmware.bin", b"\x00\x01firmware"),
        (ArtifactType.BUILD_LOG, "build.log", "build succeeded\n"),
        (ArtifactType.SERIAL_LOG, "serial.log", "temperature=24\n"),
        (
            ArtifactType.SIMULATION_OUTPUT,
            "simulation.json",
            '{"status":"complete"}',
        ),
        (
            ArtifactType.GENERATED_SOURCE_ARCHIVE,
            "sources.zip",
            b"PK\x03\x04archive",
        ),
    ],
)
def test_save_and_load_supported_artifacts(
    tmp_path: Path,
    artifact_type: ArtifactType,
    name: str,
    content: bytes | str,
) -> None:
    artifacts = service(tmp_path / "artifacts")

    metadata = run(
        artifacts.save_artifact(
            artifact_type,
            name,
            content,
            metadata={"task_id": "task-123"},
        )
    )

    expected = content.encode("utf-8") if isinstance(content, str) else content
    assert run(artifacts.load_artifact(metadata.artifact_id)) == expected
    assert metadata.size_bytes == len(expected)
    assert metadata.created_at == CREATED_AT
    assert metadata.metadata == {"task_id": "task-123"}
    assert Path(metadata.artifact_path).read_bytes() == expected


def test_content_types_are_inferred_by_artifact_kind(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")

    firmware = run(
        artifacts.save_artifact(
            ArtifactType.FIRMWARE_BINARY,
            "firmware.hex",
            b"hex",
        )
    )
    log = run(
        artifacts.save_artifact(ArtifactType.BUILD_LOG, "build.log", "log")
    )
    simulation = run(
        artifacts.save_artifact(
            ArtifactType.SIMULATION_OUTPUT,
            "result.json",
            "{}",
        )
    )

    assert firmware.content_type == "application/octet-stream"
    assert log.content_type == "text/plain; charset=utf-8"
    assert simulation.content_type == "application/json"


def test_save_imports_existing_path_without_modifying_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "firmware.bin"
    source.write_bytes(b"firmware")
    artifacts = service(tmp_path / "artifacts")

    metadata = run(
        artifacts.save_artifact(
            ArtifactType.FIRMWARE_BINARY,
            source.name,
            source,
        )
    )

    assert run(artifacts.load_artifact(metadata.artifact_id)) == b"firmware"
    assert source.read_bytes() == b"firmware"


def test_save_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")

    first = run(
        artifacts.save_artifact(
            ArtifactType.BUILD_LOG,
            "build.log",
            "same content",
            metadata={"environment": "release"},
        )
    )
    second = run(
        artifacts.save_artifact(
            ArtifactType.BUILD_LOG,
            "build.log",
            "same content",
            metadata={"environment": "release"},
        )
    )

    assert second == first
    assert first.artifact_id.startswith("artifact-")
    assert len(run(artifacts.list_artifacts())) == 1


def test_explicit_identifier_collision_is_rejected(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    run(
        artifacts.save_artifact(
            ArtifactType.BUILD_LOG,
            "build.log",
            "first",
            artifact_id="artifact-fixed",
        )
    )

    with pytest.raises(ArtifactConflictError, match="already exists"):
        run(
            artifacts.save_artifact(
                ArtifactType.BUILD_LOG,
                "build.log",
                "second",
                artifact_id="artifact-fixed",
            )
        )


def test_manifest_output_is_deterministic(tmp_path: Path) -> None:
    first = service(tmp_path / "first")
    second = service(tmp_path / "second")

    first_metadata = run(
        first.save_artifact(
            ArtifactType.SERIAL_LOG,
            "serial.log",
            "output",
            metadata={"z": 1, "a": [2, 3]},
        )
    )
    second_metadata = run(
        second.save_artifact(
            ArtifactType.SERIAL_LOG,
            "serial.log",
            "output",
            metadata={"z": 1, "a": [2, 3]},
        )
    )

    first_manifest = Path(first_metadata.artifact_path).with_name(
        "metadata.json"
    )
    second_manifest = Path(second_metadata.artifact_path).with_name(
        "metadata.json"
    )
    assert first_manifest.read_bytes() == second_manifest.read_bytes()


def test_list_artifacts_is_stable_and_filterable(tmp_path: Path) -> None:
    times = iter(
        [
            CREATED_AT + timedelta(minutes=2),
            CREATED_AT,
            CREATED_AT + timedelta(minutes=1),
        ]
    )
    artifacts = ArtifactService(
        tmp_path / "artifacts",
        clock=lambda: next(times),
    )
    run(
        artifacts.save_artifact(
            ArtifactType.SERIAL_LOG,
            "serial.log",
            "serial",
        )
    )
    build = run(
        artifacts.save_artifact(ArtifactType.BUILD_LOG, "build.log", "build")
    )
    run(
        artifacts.save_artifact(
            ArtifactType.FIRMWARE_BINARY,
            "firmware.bin",
            b"firmware",
        )
    )
    unmanaged = tmp_path / "artifacts" / "unmanaged"
    unmanaged.mkdir()

    listed = run(artifacts.list_artifacts())
    filtered = run(artifacts.list_artifacts(ArtifactType.BUILD_LOG))

    assert tuple(item.name for item in listed) == (
        "build.log",
        "firmware.bin",
        "serial.log",
    )
    assert filtered == (build,)


def test_delete_artifact_is_idempotent(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    metadata = run(
        artifacts.save_artifact(ArtifactType.BUILD_LOG, "build.log", "log")
    )

    assert run(artifacts.delete_artifact(metadata.artifact_id)) is True
    assert run(artifacts.delete_artifact(metadata.artifact_id)) is False
    with pytest.raises(ArtifactNotFoundError):
        run(artifacts.load_artifact(metadata.artifact_id))


def test_load_detects_payload_tampering(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    metadata = run(
        artifacts.save_artifact(
            ArtifactType.FIRMWARE_BINARY,
            "firmware.bin",
            b"firmware",
        )
    )
    Path(metadata.artifact_path).write_bytes(b"changed")

    with pytest.raises(ArtifactIntegrityError, match="size|checksum"):
        run(artifacts.load_artifact(metadata.artifact_id))


def test_load_rejects_invalid_manifest(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    metadata = run(
        artifacts.save_artifact(ArtifactType.BUILD_LOG, "build.log", "log")
    )
    manifest = Path(metadata.artifact_path).with_name("metadata.json")
    manifest.write_text("not json", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError, match="manifest is invalid"):
        run(artifacts.load_artifact(metadata.artifact_id))


def test_archive_artifacts_creates_reproducible_zip(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    firmware = run(
        artifacts.save_artifact(
            ArtifactType.FIRMWARE_BINARY,
            "firmware.bin",
            b"firmware",
        )
    )
    log = run(
        artifacts.save_artifact(
            ArtifactType.BUILD_LOG,
            "build.log",
            "success\n",
        )
    )

    archive = run(
        artifacts.archive_artifacts(
            [log.artifact_id, firmware.artifact_id],
            "execution.zip",
            metadata={"execution_id": "execution-123"},
        )
    )
    payload = run(artifacts.load_artifact(archive.artifact_id))

    assert archive.artifact_type is ArtifactType.GENERATED_SOURCE_ARCHIVE
    assert archive.content_type == "application/zip"
    assert archive.metadata["artifact_count"] == 2
    assert archive.metadata["artifact_ids"] == tuple(
        sorted([firmware.artifact_id, log.artifact_id])
    )
    ordered = sorted((firmware, log), key=lambda item: item.artifact_id)
    with zipfile.ZipFile(BytesIO(payload)) as zipped:
        assert zipped.namelist() == [
            "manifest.json",
            *(f"artifacts/{item.artifact_id}/{item.name}" for item in ordered),
        ]
        archive_manifest = json.loads(zipped.read("manifest.json"))
        assert [item["artifact_id"] for item in archive_manifest["artifacts"]] == [
            item.artifact_id for item in ordered
        ]
        assert zipped.read(
            f"artifacts/{firmware.artifact_id}/firmware.bin"
        ) == b"firmware"
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0)
            for item in zipped.infolist()
        )


def test_archive_content_is_independent_of_requested_id_order(
    tmp_path: Path,
) -> None:
    first = service(tmp_path / "first")
    second = service(tmp_path / "second")

    first_a = run(
        first.save_artifact(ArtifactType.BUILD_LOG, "a.log", "a")
    )
    first_b = run(
        first.save_artifact(ArtifactType.SERIAL_LOG, "b.log", "b")
    )
    second_a = run(
        second.save_artifact(ArtifactType.BUILD_LOG, "a.log", "a")
    )
    second_b = run(
        second.save_artifact(ArtifactType.SERIAL_LOG, "b.log", "b")
    )

    archive_one = run(
        first.archive_artifacts(
            [first_b.artifact_id, first_a.artifact_id],
            "bundle.zip",
        )
    )
    archive_two = run(
        second.archive_artifacts(
            [second_a.artifact_id, second_b.artifact_id],
            "bundle.zip",
        )
    )

    assert archive_one.artifact_id == archive_two.artifact_id
    assert run(first.load_artifact(archive_one.artifact_id)) == run(
        second.load_artifact(archive_two.artifact_id)
    )


@pytest.mark.parametrize(
    ("artifact_ids", "archive_name"),
    [
        ([], "bundle.zip"),
        (["artifact-a", "artifact-a"], "bundle.zip"),
        (["../bad"], "bundle.zip"),
        (["artifact-a"], "bundle.tar"),
    ],
)
def test_archive_rejects_invalid_requests(
    tmp_path: Path,
    artifact_ids: list[str],
    archive_name: str,
) -> None:
    artifacts = service(tmp_path / "artifacts")

    with pytest.raises(ValueError):
        run(artifacts.archive_artifacts(artifact_ids, archive_name))


def test_archive_rejects_missing_artifact(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")

    with pytest.raises(ArtifactNotFoundError):
        run(artifacts.archive_artifacts(["artifact-missing"], "bundle.zip"))


@pytest.mark.parametrize(
    "name",
    ["", "../firmware.bin", "dir/file.bin", "bad?.bin", "CON.txt"],
)
def test_save_rejects_unsafe_names(tmp_path: Path, name: str) -> None:
    artifacts = service(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="name"):
        run(
            artifacts.save_artifact(
                ArtifactType.FIRMWARE_BINARY,
                name,
                b"firmware",
            )
        )


@pytest.mark.parametrize(
    "artifact_id",
    ["", "../escape", "with space", "/absolute", "bad\\path"],
)
def test_operations_reject_unsafe_identifiers(
    tmp_path: Path,
    artifact_id: str,
) -> None:
    artifacts = service(tmp_path / "artifacts")
    with pytest.raises(ValueError, match="artifact_id"):
        run(artifacts.load_artifact(artifact_id))
    with pytest.raises(ValueError, match="artifact_id"):
        run(artifacts.delete_artifact(artifact_id))


def test_save_rejects_invalid_content_and_metadata(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")

    with pytest.raises(ValueError, match="content"):
        run(
            artifacts.save_artifact(
                ArtifactType.BUILD_LOG,
                "build.log",
                object(),  # type: ignore[arg-type]
            )
        )
    with pytest.raises(ValueError, match="metadata"):
        run(
            artifacts.save_artifact(
                ArtifactType.BUILD_LOG,
                "build.log",
                "log",
                metadata={"bad": object()},
            )
        )


def test_source_path_must_be_a_regular_file(tmp_path: Path) -> None:
    artifacts = service(tmp_path / "artifacts")
    directory = tmp_path / "source"
    directory.mkdir()

    with pytest.raises(ValueError, match="existing file"):
        run(
            artifacts.save_artifact(
                ArtifactType.FIRMWARE_BINARY,
                "firmware.bin",
                directory,
            )
        )


def test_artifact_directory_symlink_is_rejected_when_supported(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artifacts"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "artifact-linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available")

    artifacts = service(root)
    with pytest.raises(ArtifactIntegrityError, match="symlink"):
        run(artifacts.delete_artifact("artifact-linked"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"artifacts_root": ""},
        {"artifacts_root": object()},
        {"artifacts_root": "artifacts", "clock": object()},
    ],
)
def test_service_rejects_invalid_configuration(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ArtifactService(**kwargs)  # type: ignore[arg-type]


def test_artifacts_root_cannot_be_a_file(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    root.write_text("not a directory", encoding="utf-8")
    artifacts = service(root)

    with pytest.raises(ArtifactIntegrityError, match="directory"):
        run(artifacts.list_artifacts())
