from __future__ import annotations

import json
from pathlib import Path

import pytest

import backend.bridges.agy_scratch_project_import as module
from backend.bridges.agy_scratch_project_import import (
    AGYScratchImportClassification as C,
    AGYScratchImportError,
    AGYScratchProjectImportService,
    MARKER_NAME,
    MAX_FILE_BYTES,
    MAX_FILES,
    safe_target,
)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore


def make_service(tmp_path: Path, *, review_service: BridgeDiffService | None = None) -> tuple[AGYScratchProjectImportService, Path, Path, Path]:
    repo = tmp_path / "repo"
    active = repo / "workspace"
    scratch = tmp_path / "home" / ".gemini" / "antigravity-cli" / "scratch"
    active.mkdir(parents=True)
    scratch.mkdir(parents=True)
    (active / "ACTIVE.txt").write_text("unchanged\n", encoding="utf-8")
    state = repo / ".promptforge" / "state"
    reviews = review_service or BridgeDiffService(
        store=BridgeReviewStore(
            snapshots_path=state / "bridge-snapshots.jsonl",
            reviews_path=state / "bridge-reviews.jsonl",
        )
    )
    service = AGYScratchProjectImportService(
        repository_root=repo,
        active_workspace_root=active,
        managed_sandbox_root=repo / ".promptforge" / "agy-import-sandboxes",
        review_service=reviews,
        scratch_root=scratch,
        status_path=state / "agy-scratch-project-import-status.json",
        env={"OneDrive": str(tmp_path / "OneDrive")},
    )
    return service, scratch, active, state


def project(scratch: Path, name: str = "esp32_blink") -> Path:
    root = scratch / name
    root.mkdir(parents=True)
    return root


def rejected(service: AGYScratchProjectImportService, source: Path | str, classification: str) -> AGYScratchImportError:
    with pytest.raises(AGYScratchImportError) as caught:
        service.import_project(str(source))
    assert caught.value.classification == classification
    return caught.value


def test_missing_source_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    rejected(service, scratch / "missing", C.SOURCE_MISSING)


def test_source_outside_scratch_root_rejected(tmp_path: Path) -> None:
    service, _, _, _ = make_service(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    rejected(service, outside, C.SOURCE_OUTSIDE_ROOT)


def test_scratch_root_itself_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    rejected(service, scratch, C.SOURCE_IS_ROOT)


def test_brain_directory_rejected(tmp_path: Path) -> None:
    service, _, _, _ = make_service(tmp_path)
    brain = tmp_path / "home" / ".gemini" / "antigravity" / "brain"
    brain.mkdir(parents=True)
    rejected(service, brain, C.SOURCE_UNSAFE_PATH)


@pytest.mark.parametrize("kind", ["home", "desktop", "onedrive", "repo", "active"])
def test_sensitive_roots_rejected(tmp_path: Path, kind: str) -> None:
    service, _, active, _ = make_service(tmp_path)
    values = {
        "home": Path.home(),
        "desktop": Path.home() / "Desktop",
        "onedrive": tmp_path / "OneDrive",
        "repo": tmp_path / "repo",
        "active": active,
    }
    target = values[kind]
    if kind in {"onedrive", "desktop"} and not target.exists():
        target.mkdir(parents=True)
    rejected(service, target, C.SOURCE_UNSAFE_PATH)


def test_source_symlink_or_reparse_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    real = project(scratch, "real")
    (real / "main.py").write_text("print('ok')\n", encoding="utf-8")
    link = scratch / "linked"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("host does not permit directory links")
    rejected(service, link, C.SYMLINK_BLOCKED)


def test_child_symlink_or_reparse_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    try:
        (root / "main.py").symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit file links")
    rejected(service, root, C.SYMLINK_BLOCKED)


def test_parent_traversal_source_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    (root / "main.py").write_text("ok\n", encoding="utf-8")
    rejected(service, f"{root}\\..\\{root.name}", C.SOURCE_UNSAFE_PATH)


def test_too_many_files_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    for index in range(MAX_FILES + 1):
        (root / f"file_{index}.txt").write_text("x", encoding="utf-8")
    rejected(service, root, C.TOO_MANY_FILES)


def test_total_bytes_too_large_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    for index in range(11):
        (root / f"file_{index}.txt").write_text("x" * (500 * 1024), encoding="utf-8")
    rejected(service, root, C.TOO_LARGE)


def test_single_file_too_large_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    (root / "large.txt").write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    rejected(service, root, C.FILE_TOO_LARGE)


def test_unsupported_extension_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    (root / "firmware.bin").write_bytes(b"not accepted")
    rejected(service, root, C.UNSUPPORTED_FILE_TYPE)


@pytest.mark.parametrize("filename", [".env", ".env.local", "private.pem", "id_rsa", "auth.json", "credentials.json", "token.json", "secrets.yml"])
def test_secret_files_rejected(tmp_path: Path, filename: str) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    (root / filename).write_text("secret\n", encoding="utf-8")
    rejected(service, root, C.SECRET_FILE_BLOCKED)


def test_binary_text_extension_rejected(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    (root / "main.py").write_bytes(b"ok\x00binary")
    rejected(service, root, C.BINARY_FILE_BLOCKED)


@pytest.mark.parametrize("directory", [".git", "node_modules", ".venv", "build", ".gemini", ".codex", ".ssh"])
def test_rejected_directories_block_entire_import(tmp_path: Path, directory: str) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    blocked = root / directory
    blocked.mkdir()
    (blocked / "file.txt").write_text("blocked\n", encoding="utf-8")
    rejected(service, root, C.SOURCE_UNSAFE_PATH)


def test_maximum_depth_is_enforced(tmp_path: Path) -> None:
    service, scratch, _, _ = make_service(tmp_path)
    root = project(scratch)
    target = root.joinpath(*[f"d{i}" for i in range(8)], "file.txt")
    target.parent.mkdir(parents=True)
    target.write_text("too deep\n", encoding="utf-8")
    rejected(service, root, C.SOURCE_UNSAFE_PATH)


@pytest.mark.parametrize("relative", ["../escape.txt", "C:/escape.txt", "/escape.txt"])
def test_copy_target_rejects_parent_and_absolute_escape(tmp_path: Path, relative: str) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    with pytest.raises(AGYScratchImportError, match=C.SOURCE_UNSAFE_PATH):
        safe_target(sandbox, relative)


@pytest.mark.parametrize(
    "files",
    [
        {"platformio.ini": "[env:esp32dev]\nplatform=espressif32\n", "src/main.cpp": "void setup() {}\nvoid loop() {}\n"},
        {"nested/platformio.ini": "[env:test]\n", "nested/src/main.cpp": "int main() { return 0; }\n"},
        {"blink.ino": "void setup() {}\nvoid loop() {}\n"},
        {"CMakeLists.txt": "cmake_minimum_required(VERSION 3.16)\n", "main/main.c": "void app_main(void) {}\n"},
        {"main.py": "print('MicroPython')\n"},
    ],
)
def test_valid_project_styles_are_imported_and_reviewed(tmp_path: Path, files: dict[str, str]) -> None:
    service, scratch, active, state = make_service(tmp_path)
    root = project(scratch)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    active_before = (active / "ACTIVE.txt").read_bytes()

    result = service.import_project(str(root))

    assert result.classification == C.PASS
    assert result.file_count == len(files) == result.created_file_count
    assert result.review_created and result.review_id
    assert result.modified_file_count == result.deleted_file_count == 0
    assert (active / "ACTIVE.txt").read_bytes() == active_before
    sandbox_roots = list((tmp_path / "repo" / ".promptforge" / "agy-import-sandboxes").iterdir())
    assert len(sandbox_roots) == 1
    assert (sandbox_roots[0] / MARKER_NAME).is_file()
    for relative, content in files.items():
        assert (sandbox_roots[0] / relative).read_text(encoding="utf-8") == content
    raw = (state / "bridge-reviews.jsonl").read_text(encoding="utf-8")
    assert str(root) not in raw
    assert '"auto_apply": false' in raw and '"auto_build": false' in raw and '"auto_flash": false' in raw


def test_rejected_project_creates_no_review_or_sandbox(tmp_path: Path) -> None:
    service, scratch, _, state = make_service(tmp_path)
    root = project(scratch)
    (root / ".env").write_text("secret", encoding="utf-8")
    rejected(service, root, C.SECRET_FILE_BLOCKED)
    assert not (state / "bridge-reviews.jsonl").exists()
    assert not (tmp_path / "repo" / ".promptforge" / "agy-import-sandboxes").exists()


def test_active_workspace_change_blocks_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, scratch, _, state = make_service(tmp_path)
    root = project(scratch)
    (root / "main.py").write_text("ok\n", encoding="utf-8")
    calls = 0
    original = module.snapshot_integrity

    def changed(path: Path):
        nonlocal calls
        calls += 1
        value = original(path)
        return value if calls == 1 else value + (("changed", "changed"),)

    monkeypatch.setattr(module, "snapshot_integrity", changed)
    rejected(service, root, C.ACTIVE_WORKSPACE_UNSAFE)
    assert not (state / "bridge-reviews.jsonl").exists()


def test_status_and_review_metadata_are_sanitized(tmp_path: Path) -> None:
    service, scratch, _, state = make_service(tmp_path)
    root = project(scratch, "safe_name")
    (root / "main.py").write_text("ok\n", encoding="utf-8")
    result = service.import_project(str(root))
    status = json.loads((state / "agy-scratch-project-import-status.json").read_text(encoding="utf-8"))
    assert status["source_name"] == "safe_name"
    assert str(root) not in json.dumps(status)
    assert status["review_id"] == result.review_id


def test_implementation_has_no_provider_execution_or_automatic_discovery() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "Popen", "spawn", "newest", "glob(", "rglob("):
        assert forbidden not in source
