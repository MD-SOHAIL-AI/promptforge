from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.sandbox_service import BridgeSandboxService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_sandbox_copy_ignores_heavy_folders(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "code\n")
    write(workspace / ".git" / "config", "git\n")
    write(workspace / ".pio" / "build.bin", "pio\n")
    write(workspace / "node_modules" / "pkg" / "index.js", "node\n")
    service = BridgeSandboxService(tmp_path / "sandboxes")

    sandbox = service.create_sandbox(run_id="run-1", workspace_root=workspace)

    assert (sandbox / "src" / "main.cpp").exists()
    assert not (sandbox / ".git").exists()
    assert not (sandbox / ".pio").exists()
    assert not (sandbox / "node_modules").exists()


def test_sandbox_copy_does_not_follow_unsafe_symlink_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside-secret.txt"
    write(workspace / "README.md", "safe\n")
    outside.write_text("secret\n", encoding="utf-8")
    try:
        (workspace / "linked-secret.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    service = BridgeSandboxService(tmp_path / "sandboxes")

    sandbox = service.create_sandbox(run_id="run-1", workspace_root=workspace)

    assert (sandbox / "README.md").exists()
    assert not (sandbox / "linked-secret.txt").exists()
