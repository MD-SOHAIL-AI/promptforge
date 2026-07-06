from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.integration.bridge_patch_safety_helpers import (
    assert_workspace_unchanged,
    conflict_types,
    make_manual_patch_record,
    make_rig,
    workspace_hashes,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"C:\Users\evil.txt", "path_unsafe"),
        (r"C:Users\evil.txt", "path_unsafe"),
        (r"..\evil.txt", "path_unsafe"),
        (r"..\..\secret.txt", "path_unsafe"),
        (r".git\config", "ignored_path"),
        (r".PIO\build\firmware.bin", "ignored_path"),
        (r"node_modules\pkg\index.js", "ignored_path"),
        (r"dist\bundle.js", "ignored_path"),
        (r"build\firmware.bin", "ignored_path"),
        (r".next\cache\entry", "ignored_path"),
        (r".forgex\state.json", "ignored_path"),
    ],
)
def test_windows_absolute_escape_and_ignored_paths_are_blocked(tmp_path: Path, path: str, expected: str) -> None:
    rig = make_rig(tmp_path)
    patch_id = make_manual_patch_record(rig, patch_id=f"windows-{abs(hash(path))}.patch", path=path, safe=expected != "path_unsafe")
    before = workspace_hashes(rig.workspace)

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.can_apply is False
    assert expected in conflict_types(result)
    assert_workspace_unchanged(rig.workspace, before)


@pytest.mark.skipif(os.name != "nt", reason="Backslash target lookup is Windows-specific")
def test_backslash_relative_paths_are_normalized_for_safe_workspace_targets(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    patch_id = make_manual_patch_record(
        rig,
        patch_id="windows-safe-backslash.patch",
        path=r"src\main.cpp",
        change_type="modified",
        safe=True,
    )
    before = workspace_hashes(rig.workspace)

    result = rig.preflight.preflight_patch(patch_id, workspace_root=rig.workspace)

    assert result.can_apply is True
    assert result.apply_enabled is False
    assert_workspace_unchanged(rig.workspace, before)
