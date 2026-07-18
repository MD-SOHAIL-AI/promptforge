from __future__ import annotations

from pathlib import Path

import pytest

from backend.agent_runtime.api_coding_context import build_api_coding_context


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def base_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write(workspace / "platformio.ini", "[env:esp32dev]\nplatform = espressif32\n")
    write(workspace / "src" / "main.cpp", "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n")
    write(workspace / "include" / "config.h", "#pragma once\n")
    write(workspace / "README.md", "# Blink\n")
    return workspace


def excluded_reasons(context: object) -> dict[str, str]:
    return {item.path: item.reason for item in context.excluded_files}  # type: ignore[attr-defined]


def included_paths(context: object) -> list[str]:
    return [item.path for item in context.files]  # type: ignore[attr-defined]


def test_selected_files_default_includes_embedded_project_context(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)

    context = build_api_coding_context(workspace, prompt="basic esp32 blink")

    assert context.context_mode == "selected_files"
    assert context.workspace_label == "workspace"
    assert "platformio.ini" in included_paths(context)
    assert "src/main.cpp" in included_paths(context)
    assert "include/config.h" in included_paths(context)
    assert "README.md" in included_paths(context)
    assert context.total_bytes > 0
    assert context.truncated is False
    assert context.tree_summary == ()


def test_selected_files_includes_only_explicit_safe_files(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / "src" / "other.cpp", "int other = 1;\n")

    context = build_api_coding_context(
        workspace,
        prompt="change one file",
        selected_files=["src/other.cpp"],
    )

    assert included_paths(context) == ["src/other.cpp"]
    assert context.files[0].content.replace("\r\n", "\n") == "int other = 1;\n"


def test_project_summary_includes_tree_and_key_config_files(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / "package.json", '{"scripts":{}}\n')
    write(workspace / "src" / "feature.cpp", "int feature = 1;\n")

    context = build_api_coding_context(
        workspace,
        prompt="summarize",
        context_mode="project_summary",
    )

    assert "platformio.ini" in included_paths(context)
    assert "package.json" in included_paths(context)
    assert "src/main.cpp" not in included_paths(context)
    assert "src/main.cpp" in context.tree_summary
    assert "src/feature.cpp" in context.tree_summary


@pytest.mark.parametrize(
    ("selected", "reason"),
    [
        ("../evil.txt", "path_traversal"),
        ("/tmp/evil.txt", "absolute_path"),
        ("C:\\Users\\evil.txt", "unsafe_path_separator"),
        ("\\\\server\\share\\secret.txt", "absolute_or_unsafe_path"),
    ],
)
def test_rejects_unsafe_selected_paths(tmp_path: Path, selected: str, reason: str) -> None:
    workspace = base_workspace(tmp_path)

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=[selected],
    )

    assert context.files == ()
    assert reason in excluded_reasons(context).values()


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = workspace / "src" / "outside.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable on this filesystem: {exc}")

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=["src/outside.txt"],
    )

    assert excluded_reasons(context)["src/outside.txt"] == "symlink_escape"
    assert context.files == ()


def test_excludes_sensitive_paths_and_secret_content(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / ".env", "OPENAI_API_KEY=sk-test\n")
    write(workspace / "private.pem", "-----BEGIN PRIVATE KEY-----\nsecret\n")
    write(workspace / "src" / "api.cpp", "const char* key = \"ok\";\nOPENAI_API_KEY=sk-test\n")
    write(workspace / "src" / "bearer.cpp", "Authorization: Bearer abcdefghijklmnop\n")
    write(workspace / "src" / "password.cpp", "password: hunter2\n")
    write(workspace / "src" / "token.cpp", "token = abc123\n")

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=[
            ".env",
            "private.pem",
            "src/api.cpp",
            "src/bearer.cpp",
            "src/password.cpp",
            "src/token.cpp",
        ],
    )
    reasons = excluded_reasons(context)

    assert reasons[".env"] == "sensitive_path"
    assert reasons["private.pem"] == "sensitive_path"
    assert reasons["src/api.cpp"] == "secret_detected"
    assert reasons["src/bearer.cpp"] == "secret_detected"
    assert reasons["src/password.cpp"] == "secret_detected"
    assert reasons["src/token.cpp"] == "secret_detected"
    assert context.files == ()


def test_size_limits_are_enforced_and_mark_truncated(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / "src" / "large.cpp", "x" * 128)
    write(workspace / "src" / "one.cpp", "1" * 20)
    write(workspace / "src" / "two.cpp", "2" * 20)
    write(workspace / "src" / "three.cpp", "3" * 20)

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=["src/large.cpp", "src/one.cpp", "src/two.cpp", "src/three.cpp"],
        max_files=2,
        max_file_bytes=64,
        max_total_bytes=35,
    )
    reasons = excluded_reasons(context)

    assert reasons["src/large.cpp"] == "max_file_bytes_exceeded"
    assert reasons["src/two.cpp"] == "max_total_bytes_exceeded"
    assert reasons["src/three.cpp"] == "max_total_bytes_exceeded"
    assert included_paths(context) == ["src/one.cpp"]
    assert context.truncated is True


def test_max_file_count_is_enforced(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / "src" / "a.cpp", "a\n")
    write(workspace / "src" / "b.cpp", "b\n")

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=["src/a.cpp", "src/b.cpp"],
        max_files=1,
    )

    assert included_paths(context) == ["src/a.cpp"]
    assert excluded_reasons(context)["src/b.cpp"] == "max_files_exceeded"
    assert context.truncated is True


def test_generated_folders_and_binary_files_are_skipped(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)
    write(workspace / ".git" / "config", "private\n")
    write(workspace / "node_modules" / "pkg" / "index.js", "module.exports = {}\n")
    write(workspace / ".pio" / "build" / "firmware.bin", "artifact\n")
    write(workspace / "build" / "out.cpp", "generated\n")
    write(workspace / "dist" / "bundle.js", "generated\n")
    write(workspace / "__pycache__" / "x.pyc", "generated\n")
    (workspace / "src" / "image.bin").write_bytes(b"\x00\x01\x02")

    context = build_api_coding_context(
        workspace,
        prompt="safe",
        selected_files=[
            ".git/config",
            "node_modules/pkg/index.js",
            ".pio/build/firmware.bin",
            "build/out.cpp",
            "dist/bundle.js",
            "__pycache__/x.pyc",
            "src/image.bin",
        ],
    )
    reasons = excluded_reasons(context)

    assert reasons[".git/config"] == "generated_or_ignored_path"
    assert reasons["node_modules/pkg/index.js"] == "generated_or_ignored_path"
    assert reasons[".pio/build/firmware.bin"] == "generated_or_ignored_path"
    assert reasons["build/out.cpp"] == "generated_or_ignored_path"
    assert reasons["dist/bundle.js"] == "generated_or_ignored_path"
    assert reasons["__pycache__/x.pyc"] == "generated_or_ignored_path"
    assert reasons["src/image.bin"] == "unsupported_file_type"
    assert context.files == ()


def test_context_builder_does_not_persist_workflow_state(tmp_path: Path) -> None:
    workspace = base_workspace(tmp_path)

    context = build_api_coding_context(workspace, prompt="safe")

    assert context.files
    assert not (workspace / ".promptforge" / "state" / "coding-workflow-runs.jsonl").exists()
    assert not (workspace / ".promptforge" / "state" / "coding-workflow-events.jsonl").exists()
