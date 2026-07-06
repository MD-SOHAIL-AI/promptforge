from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.patch_apply_engine import PatchApplyEngine, PatchApplyEngineError


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_engine_stages_modify_create_and_delete(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src/main.cpp", "old\nkeep\n")
    write(workspace / "README.md", "remove\n")
    patch = """diff --git a/src/main.cpp b/src/main.cpp
--- a/src/main.cpp
+++ b/src/main.cpp
@@ -1,2 +1,2 @@
-old
+new
 keep
diff --git a/src/new.cpp b/src/new.cpp
--- a/src/new.cpp
+++ b/src/new.cpp
@@ -0,0 +1 @@
+created
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +0,0 @@
-remove
"""

    staged = PatchApplyEngine().stage(
        patch,
        workspace_root=workspace,
        files_to_create=("src/new.cpp",),
        files_to_modify=("src/main.cpp",),
        files_to_delete=("README.md",),
    )

    by_path = {item.path: item for item in staged}
    assert by_path["src/main.cpp"].content == b"new\nkeep\n"
    assert by_path["src/new.cpp"].content == b"created\n"
    assert by_path["README.md"].content is None


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/../escape.cpp b/../escape.cpp\n--- a/../escape.cpp\n+++ b/../escape.cpp\n@@ -0,0 +1 @@\n+x\n",
        "diff --git a/node_modules/pkg/index.js b/node_modules/pkg/index.js\n--- a/node_modules/pkg/index.js\n+++ b/node_modules/pkg/index.js\n@@ -0,0 +1 @@\n+x\n",
        "diff --git a/a.cpp b/b.cpp\n--- a/a.cpp\n+++ b/b.cpp\n@@ -0,0 +1 @@\n+x\n",
        "diff --git a/file.bin b/file.bin\nGIT binary patch\n",
        "diff --git a/old.cpp b/new.cpp\nrename from old.cpp\nrename to new.cpp\n",
        "diff --git a/main.cpp b/main.cpp\nold mode 100644\nnew mode 100755\n",
    ],
)
def test_engine_rejects_unsupported_or_unsafe_patches(tmp_path: Path, patch: str) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(PatchApplyEngineError):
        PatchApplyEngine().stage(
            patch,
            workspace_root=workspace,
            files_to_create=("node_modules/pkg/index.js", "../escape.cpp", "b.cpp", "file.bin", "new.cpp", "main.cpp"),
            files_to_modify=(),
            files_to_delete=(),
        )


def test_engine_rejects_failed_context_match(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src/main.cpp", "changed\n")
    patch = """diff --git a/src/main.cpp b/src/main.cpp
--- a/src/main.cpp
+++ b/src/main.cpp
@@ -1 +1 @@
-old
+new
"""

    with pytest.raises(PatchApplyEngineError, match="context"):
        PatchApplyEngine().stage(
            patch,
            workspace_root=workspace,
            files_to_create=(),
            files_to_modify=("src/main.cpp",),
            files_to_delete=(),
        )
