from __future__ import annotations

import pytest

from backend.contracts.generated_project import GeneratedFile
from backend.services.chunked_generation_writer import ChunkedGenerationWriter
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore


def test_chunked_writer_rejects_unsafe_paths(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    writer = ChunkedGenerationWriter(workspace)

    with pytest.raises(ValueError):
        writer.write_file(GeneratedFile(path="../escape.txt", content="nope"))

    assert not (tmp_path / "escape.txt").exists()


def test_generation_diagnostics_store_persists_chunked_runs(tmp_path) -> None:
    store = GenerationDiagnosticsStore(tmp_path)
    store.record_chunked_run(
        {
            "run_id": "run-1",
            "execution_id": "exec-1",
            "project_id": "project-1",
            "status": "success",
            "file_statuses": [{"path": "platformio.ini", "status": "written"}],
        }
    )

    reloaded = GenerationDiagnosticsStore(tmp_path)
    runs = reloaded.list_chunked_runs(execution_id="exec-1")

    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["file_statuses"][0]["path"] == "platformio.ini"
