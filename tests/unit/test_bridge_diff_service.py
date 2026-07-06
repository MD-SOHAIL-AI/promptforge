from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.diff_service import BridgeDiffError, BridgeDiffService
from backend.bridges.review_models import BridgeSnapshotFile, BridgeWorkspaceSnapshot


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_snapshot_ignores_heavy_folders(tmp_path: Path) -> None:
    write(tmp_path / "src" / "main.cpp", "void setup() {}\n")
    write(tmp_path / ".git" / "config", "secret\n")
    write(tmp_path / ".pio" / "build.bin", "heavy\n")
    write(tmp_path / "node_modules" / "pkg" / "index.js", "heavy\n")
    service = BridgeDiffService()

    snapshot = service.snapshot_workspace(tmp_path)

    assert "src/main.cpp" in snapshot.files
    assert ".git/config" not in snapshot.files
    assert ".pio/build.bin" not in snapshot.files
    assert "node_modules/pkg/index.js" not in snapshot.files


def test_snapshot_rejects_path_outside_workspace(tmp_path: Path) -> None:
    service = BridgeDiffService()

    with pytest.raises(BridgeDiffError):
        service.snapshot_workspace(tmp_path / "missing")


def test_diff_detects_created_modified_and_deleted_files(tmp_path: Path) -> None:
    write(tmp_path / "src" / "main.cpp", "old\n")
    write(tmp_path / "README.md", "delete me\n")
    service = BridgeDiffService()
    snapshot = service.snapshot_workspace(tmp_path)

    write(tmp_path / "src" / "main.cpp", "new\n")
    write(tmp_path / "platformio.ini", "[env]\n")
    (tmp_path / "README.md").unlink()

    changes = service.diff_snapshot(snapshot)
    by_path = {change.path: change for change in changes}

    assert by_path["src/main.cpp"].change_type == "modified"
    assert "-old" in (by_path["src/main.cpp"].diff_preview or "")
    assert "+new" in (by_path["src/main.cpp"].diff_preview or "")
    assert by_path["platformio.ini"].change_type == "created"
    assert by_path["README.md"].change_type == "deleted"


def test_unsafe_snapshot_path_is_rejected_in_diff(tmp_path: Path) -> None:
    service = BridgeDiffService()
    snapshot = BridgeWorkspaceSnapshot(
        str(tmp_path),
        {
            "../escape.txt": BridgeSnapshotFile(
                path="../escape.txt",
                hash="old",
                size=1,
                mtime="2026-01-01T00:00:00Z",
            )
        },
    )

    changes = service.diff_snapshot(snapshot)

    assert changes[0].safe is False
    assert changes[0].warning == "Unsafe path rejected."


def test_large_diff_is_capped(tmp_path: Path) -> None:
    write(tmp_path / "src" / "main.cpp", "old\n")
    service = BridgeDiffService(max_diff_chars=80)
    snapshot = service.snapshot_workspace(tmp_path)
    write(tmp_path / "src" / "main.cpp", "\n".join(f"line {index}" for index in range(100)))

    changes = service.diff_snapshot(snapshot)

    assert changes[0].diff_preview is not None
    assert "[diff truncated]" in changes[0].diff_preview


def test_review_session_can_be_approved_and_rejected(tmp_path: Path) -> None:
    write(tmp_path / "src" / "main.cpp", "old\n")
    service = BridgeDiffService()
    snapshot = service.snapshot_workspace(tmp_path)
    write(tmp_path / "src" / "main.cpp", "new\n")

    approved_review = service.create_review(
        provider_id="codex_bridge",
        workspace_root=tmp_path,
        snapshot=snapshot,
    )
    updated, decision = service.approve_review(approved_review.review_id)

    assert updated.status == "approved"
    assert decision.decision == "approved"

    snapshot_2 = service.snapshot_workspace(tmp_path)
    write(tmp_path / "src" / "main.cpp", "newer\n")
    rejected_review = service.create_review(
        provider_id="codex_bridge",
        workspace_root=tmp_path,
        snapshot=snapshot_2,
    )
    updated, decision = service.reject_review(rejected_review.review_id)

    assert updated.status == "rejected"
    assert decision.decision == "rejected"
