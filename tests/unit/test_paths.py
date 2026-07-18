from __future__ import annotations

from backend.utils.paths import PathManager


def test_path_manager_creates_managed_directories(tmp_path) -> None:
    manager = PathManager(tmp_path / "promptforge")

    assert manager.project_root() == (tmp_path / "promptforge").resolve()
    assert manager.projects_directory().is_dir()
    assert manager.artifacts_directory().is_dir()
    assert manager.logs_directory().is_dir()
    assert manager.state_directory().is_dir()
    assert manager.projects_directory().parent.name == ".promptforge"


def test_path_manager_exposes_workspace_directories(tmp_path) -> None:
    manager = PathManager(tmp_path)

    assert manager.workspace_directory() == (tmp_path / "workspace").resolve()
    assert manager.workspace_projects_directory().is_dir()
    assert manager.workspace_builds_directory().is_dir()
    assert manager.workspace_artifacts_directory().is_dir()
    assert manager.workspace_logs_directory().is_dir()


def test_path_manager_can_split_code_root_from_runtime_data_root(tmp_path) -> None:
    manager = PathManager(
        tmp_path / "app-root",
        data_root=tmp_path / "runtime-data",
    )

    assert manager.project_root() == (tmp_path / "app-root").resolve()
    assert manager.state_directory() == (
        tmp_path / "runtime-data" / ".promptforge" / "state"
    ).resolve()
    assert manager.workspace_directory() == (
        tmp_path / "runtime-data" / "workspace"
    ).resolve()
