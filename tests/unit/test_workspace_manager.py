from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile

import pytest

from backend.observability.runtime_logs import RuntimeLogger
from backend.state.persistence import PersistenceManager
from backend.utils.paths import PathManager
from backend.workspace.workspace_manager import (
    WorkspaceManager,
    WorkspacePathError,
)


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def manager(tmp_path: Path, **kwargs: object) -> WorkspaceManager:
    return WorkspaceManager(
        PathManager(tmp_path),
        clock=kwargs.pop("clock", lambda: NOW),
        **kwargs,  # type: ignore[arg-type]
    )


def test_project_lifecycle_and_archive(tmp_path: Path) -> None:
    workspace = manager(tmp_path)

    created = workspace.create_project(
        "weather-station",
        project_id="project-1",
        task_id="task-1",
        execution_id="execution-1",
        target_board="ESP32",
        framework="PlatformIO",
        platformio_ini="[env:esp32]\nplatform = espressif32\n",
    )

    project_path = Path(created["project_path"])
    assert workspace.project_exists("weather-station") is True
    assert (project_path / "src").is_dir()
    assert (project_path / "include").is_dir()
    assert (project_path / "platformio.ini").read_text(encoding="utf-8") == (
        "[env:esp32]\nplatform = espressif32\n"
    )
    assert workspace.project_metadata("weather-station")["project_id"] == (
        "project-1"
    )
    assert [item["project_name"] for item in workspace.list_projects()] == [
        "weather-station"
    ]

    archive = workspace.archive_project("weather-station")
    with ZipFile(archive) as payload:
        assert "weather-station/metadata.json" in payload.namelist()
        archived_metadata = json.loads(
            payload.read("weather-station/metadata.json")
        )
    assert archived_metadata["status"] == "ARCHIVED"
    assert workspace.project_metadata("weather-station")["status"] == (
        "ARCHIVED"
    )

    assert workspace.delete_project("weather-station") is True
    assert workspace.delete_project("weather-station") is False
    assert workspace.project_exists("weather-station") is False


@pytest.mark.parametrize(
    "name",
    ["../escape", "project/name", "C:\\absolute", ".", "..", "bad name"],
)
def test_project_names_cannot_escape_workspace(
    tmp_path: Path,
    name: str,
) -> None:
    workspace = manager(tmp_path)

    with pytest.raises(WorkspacePathError):
        workspace.create_project(name)


def test_build_storage_and_latest_build(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    workspace = manager(tmp_path, clock=clock)

    first = workspace.save_build(
        "execution-1",
        {
            "firmware.bin": b"bin-one",
            "firmware.elf": b"elf-one",
        },
        metadata={"status": "SUCCESS"},
    )
    clock.value += timedelta(seconds=1)
    second = workspace.save_build(
        "execution-2",
        "firmware.bin",
        b"bin-two",
        metadata={"status": "SUCCESS"},
    )

    assert workspace.get_build("execution-1", "firmware.bin") == b"bin-one"
    assert Path(first["path"], "metadata.json").is_file()
    assert [item["execution_id"] for item in workspace.list_builds()] == [
        "execution-1",
        "execution-2",
    ]
    assert workspace.latest_build() == second


def test_artifact_storage(tmp_path: Path) -> None:
    workspace = manager(tmp_path)

    generated = workspace.save_artifact(
        "execution-1",
        "generated_files.json",
        {"files": ["src/main.cpp"]},
    )
    workspace.save_artifact(
        "execution-1",
        "validation.json",
        '{"valid":true}\n',
    )

    assert json.loads(
        workspace.get_artifact(
            "execution-1",
            "generated_files.json",
        )
    ) == {"files": ["src/main.cpp"]}
    assert workspace.list_artifacts("execution-1") == (
        generated,
        generated.with_name("validation.json"),
    )


def test_log_storage_appends_and_lists(tmp_path: Path) -> None:
    workspace = manager(tmp_path)

    workflow = workspace.save_log("execution-1", "workflow", "started")
    workspace.save_log("execution-1", "workflow.log", "completed\n")
    workspace.save_log("execution-1", "build", b"compiled")

    assert workspace.get_logs("execution-1", "workflow") == (
        "started\ncompleted\n"
    )
    assert workspace.get_logs("execution-1") == {
        "build": "compiled\n",
        "workflow": "started\ncompleted\n",
    }
    assert workflow in workspace.list_logs("execution-1")


def test_retention_cleanup_removes_only_expired_execution_directories(
    tmp_path: Path,
) -> None:
    workspace = manager(tmp_path)
    workspace.save_build("old", "firmware.bin", b"old")
    workspace.save_build("new", "firmware.bin", b"new")
    workspace.save_artifact("old", "validation.json", "{}")
    workspace.save_artifact("new", "validation.json", "{}")
    workspace.save_log("old", "workflow", "old")
    workspace.save_log("new", "workflow", "new")

    old_timestamp = (NOW - timedelta(days=31)).timestamp()
    for category in ("builds", "artifacts", "logs"):
        os.utime(tmp_path / "workspace" / category / "old", (old_timestamp,) * 2)

    assert workspace.cleanup_builds(30) == 1
    assert workspace.cleanup_artifacts(30) == 1
    assert workspace.cleanup_logs(30) == 1
    assert [item["execution_id"] for item in workspace.list_builds()] == [
        "new"
    ]
    assert len(workspace.list_artifacts()) == 1
    assert len(workspace.list_logs()) == 1


def test_workspace_health_counts_managed_entries(tmp_path: Path) -> None:
    workspace = manager(tmp_path)
    workspace.create_project("alpha")
    workspace.save_build("execution-1", "firmware.bin", b"firmware")
    workspace.save_artifact("execution-1", "validation.json", "{}")
    workspace.save_log("execution-1", "workflow", "started")

    health = workspace.workspace_health()

    assert health["project_count"] == 1
    assert health["build_count"] == 1
    assert health["artifact_count"] == 1
    assert health["log_count"] == 1
    assert health["total_size_bytes"] > 0


def test_persistence_and_runtime_logger_integrations(tmp_path: Path) -> None:
    persistence = PersistenceManager(tmp_path / "state.db", clock=lambda: NOW)
    runtime = RuntimeLogger(logging.Logger("workspace"), clock=lambda: NOW)
    workspace = manager(
        tmp_path,
        persistence=persistence,
        runtime_logger=runtime,
    )

    workspace.create_project(
        "alpha",
        project_id="project-1",
        task_id="task-1",
        execution_id="execution-1",
    )
    workspace.save_build("execution-1", "firmware.bin", b"firmware")
    artifact = workspace.save_artifact(
        "execution-1",
        "build_report.json",
        {"success": True},
    )

    assert persistence.load_project("project-1")["project_name"] == "alpha"
    assert persistence.load_build("execution-1")["path"].endswith(
        "execution-1"
    )
    with persistence.connection() as connection:
        row = connection.execute(
            "SELECT artifact_path FROM artifact_metadata"
        ).fetchone()
    assert row["artifact_path"] == str(artifact)
    assert runtime.log_count == 2
