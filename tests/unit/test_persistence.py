from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.state.persistence import PersistenceManager, SCHEMA_VERSION


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def manager(path: Path | str) -> PersistenceManager:
    return PersistenceManager(path, clock=lambda: NOW)


def test_initializes_schema_and_round_trips_all_record_types(
    tmp_path: Path,
) -> None:
    persistence = manager(tmp_path / "state.db")

    assert persistence.save_project(
        {"project_id": "project-1", "project_name": "alpha"}
    ) == "project-1"
    assert persistence.save_execution(
        "execution-1",
        {"status": "SUCCESS"},
        project_id="project-1",
    ) == "execution-1"
    build_id = persistence.save_build(
        {"status": "SUCCESS"},
        project_id="project-1",
        execution_id="execution-1",
    )
    validation_id = persistence.save_validation(
        {"valid": True, "issues": []},
        project_id="project-1",
    )
    assert persistence.save_artifact_metadata(
        {"artifact_id": "artifact-1", "artifact_path": "firmware.bin"},
        build_id=build_id,
    ) == "artifact-1"

    assert persistence.load_project("project-1") == {
        "project_id": "project-1",
        "project_name": "alpha",
    }
    assert persistence.load_execution("execution-1") == {"status": "SUCCESS"}
    assert persistence.load_build(build_id) == {"status": "SUCCESS"}
    assert validation_id.startswith("validation-")
    assert persistence.health_check() is True

    with persistence.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_upsert_is_deterministic_and_preserves_created_at(tmp_path: Path) -> None:
    persistence = manager(tmp_path / "state.db")
    persistence.save_project(
        {
            "project_id": "project-1",
            "project_name": "zeta",
            "created_at": "2025-01-01T00:00:00Z",
        }
    )
    persistence.save_project(
        {"project_id": "project-1", "project_name": "alpha"}
    )

    with persistence.connection() as connection:
        row = connection.execute(
            "SELECT created_at, payload_json FROM projects WHERE project_id = ?",
            ("project-1",),
        ).fetchone()

    assert row["created_at"] == "2025-01-01T00:00:00Z"
    assert row["payload_json"] == (
        '{"project_id":"project-1","project_name":"alpha"}'
    )


def test_transaction_rolls_back_and_nested_savepoint_isolated() -> None:
    persistence = manager(":memory:")

    with persistence.transaction() as connection:
        persistence.save_project({"project_id": "kept"})
        with pytest.raises(sqlite3.IntegrityError):
            with persistence.transaction() as nested:
                nested.execute(
                    "INSERT INTO projects "
                    "(project_id, payload_json, created_at, updated_at) "
                    "VALUES (?, NULL, ?, ?)",
                    ("rejected", "now", "now"),
                )

    assert persistence.load_project("kept") == {"project_id": "kept"}
    assert persistence.load_project("rejected") is None


def test_concurrent_writes_are_serialized(tmp_path: Path) -> None:
    persistence = manager(tmp_path / "state.db")

    def save(index: int) -> str:
        return persistence.save_project(
            {
                "project_id": f"project-{index:03d}",
                "project_name": f"project-{index:03d}",
            }
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        identifiers = tuple(executor.map(save, range(40)))

    assert identifiers == tuple(f"project-{index:03d}" for index in range(40))
    assert len(persistence.list_projects()) == 40


def test_context_manager_closes_and_reopens_file_database(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with manager(path) as persistence:
        persistence.save_project({"project_id": "project-1"})

    reopened = manager(path)
    assert reopened.load_project("project-1") == {"project_id": "project-1"}
    reopened.close()
