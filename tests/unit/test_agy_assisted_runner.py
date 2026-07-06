from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

import backend.bridges.agy_assisted_runner as module
from backend.bridges.agy_assisted_runner import (
    AGYAssistedClassification as C,
    AGYAssistedRunner,
    AGY_INVOCATION_MARKER,
    ProcessResult,
    SUPPORTED_TEMPLATE,
    safe_environment,
)
from backend.bridges.agy_scratch_project_import import (
    AGYScratchProjectImportService,
    ValidatedFile,
    detect_project_type,
)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_store import BridgeReviewStore


class FakeAGY:
    def __init__(self, scratch: Path, mode: str = "pass") -> None:
        self.scratch = scratch
        self.mode = mode
        self.calls: list[tuple[list[str], Path, int]] = []
        self.raw_instruction: str | None = None

    def __call__(self, args, cwd: Path, timeout: int, env: dict[str, str]) -> ProcessResult:
        del env
        values = list(args)
        self.calls.append((values, cwd, timeout))
        if values[1:] == ["--version"]:
            return ProcessResult(0, "agy 1.0.14\n", "")
        self.raw_instruction = values[2]
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(values, timeout)
        if self.mode == "provider_error":
            return ProcessResult(1, "provider failed", "provider failed")
        match = re.search(r"forgex_agy_agy-[a-f0-9]{32}_[a-f0-9]{16}", values[2])
        assert match
        expected = self.scratch / match.group(0)
        if self.mode == "missing":
            alternative = self.scratch / "unrelated_alternative"
            alternative.mkdir()
            (alternative / "main.py").write_text("not trusted\n", encoding="utf-8")
            return ProcessResult(0, f"created {alternative}", "")
        if self.mode == "cwd_write":
            (cwd / "unexpected.txt").write_text("unsafe\n", encoding="utf-8")
            return ProcessResult(0, "", "")
        if self.mode == "expected_symlink":
            outside = self.scratch.parent / "outside"
            outside.mkdir()
            (outside / "main.py").write_text("outside\n", encoding="utf-8")
            expected.symlink_to(outside, target_is_directory=True)
            return ProcessResult(0, "", "")
        expected.mkdir()
        if self.mode == "secret":
            (expected / ".env").write_text("SECRET=value\n", encoding="utf-8")
        elif self.mode == "binary":
            (expected / "main.py").write_bytes(b"text\x00binary")
        else:
            (expected / "src").mkdir()
            (expected / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\n", encoding="utf-8")
            (expected / "src" / "main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
            (expected / "README.md").write_text("ESP32 blink\n", encoding="utf-8")
        return ProcessResult(0, "raw output must not persist", "raw error must not persist")


def fixture(tmp_path: Path, *, mode: str = "pass", invocation_root: Path | None = None, enabled: bool = True):
    repo = tmp_path / "repo"
    active = repo / "workspace"
    scratch = tmp_path / "provider-home" / ".gemini" / "antigravity-cli" / "scratch"
    active.mkdir(parents=True)
    scratch.mkdir(parents=True)
    (active / "ACTIVE.txt").write_text("unchanged\n", encoding="utf-8")
    state = repo / ".promptforge" / "state"
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=state / "bridge-snapshots.jsonl",
        reviews_path=state / "bridge-reviews.jsonl",
    ))
    importer = AGYScratchProjectImportService(
        repository_root=repo,
        active_workspace_root=active,
        managed_sandbox_root=repo / ".promptforge" / "agy-import-sandboxes",
        review_service=reviews,
        scratch_root=scratch,
        status_path=state / "agy-scratch-project-import-status.json",
        env={},
    )
    fake = FakeAGY(scratch, mode)
    runner = AGYAssistedRunner(
        repository_root=repo,
        active_workspace_root=active,
        import_service=importer,
        invocation_root=invocation_root or tmp_path / "external-runs",
        feature_enabled=enabled,
        process_runner=fake,
        executable_resolver=lambda command: "C:/tools/agy.exe" if command == "agy" else None,
        status_path=state / "agy-assisted-runner-status.json",
        env={"OneDrive": str(tmp_path / "OneDrive")},
        home_root=tmp_path / "safe-home",
    )
    return runner, fake, repo, active, scratch, state, reviews


def test_runner_disabled_without_flag(tmp_path: Path) -> None:
    runner, fake, *_ = fixture(tmp_path, enabled=False)
    result = runner.run()
    assert result.classification == C.UNSAFE_ABORTED
    assert fake.calls == []


def test_only_safe_template_is_supported(tmp_path: Path) -> None:
    runner, fake, *_ = fixture(tmp_path)
    assert runner.run("arbitrary").classification == C.UNSAFE_ABORTED
    assert fake.calls == []


@pytest.mark.parametrize("target", ["repo", "active", "home", "desktop", "onedrive", "filesystem"])
def test_invocation_cwd_rejects_protected_roots(tmp_path: Path, target: str) -> None:
    base_runner, _, repo, active, _, _, _ = fixture(tmp_path)
    choices = {
        "repo": repo,
        "active": active,
        "home": tmp_path / "safe-home",
        "desktop": tmp_path / "safe-home" / "Desktop",
        "onedrive": tmp_path / "OneDrive",
        "filesystem": Path(repo.anchor),
    }
    choices[target].mkdir(parents=True, exist_ok=True)
    runner, fake, *_ = fixture(tmp_path / f"case-{target}", invocation_root=choices[target])
    runner.repository_root = repo
    runner.active_workspace_root = active
    runner.home_root = tmp_path / "safe-home"
    runner.env = {"OneDrive": str(tmp_path / "OneDrive")}
    result = runner.run()
    assert result.classification == C.UNSAFE_ABORTED
    assert all(call[0][1:] == ["--version"] for call in fake.calls) or fake.calls == []


def test_direct_argv_and_external_marked_cwd(tmp_path: Path) -> None:
    runner, fake, repo, active, _, _, _ = fixture(tmp_path)
    result = runner.run()
    assert result.classification == C.PASS
    execution = fake.calls[1]
    assert execution[0][1] == "-p" and len(execution[0]) == 3
    assert execution[1] != repo and execution[1] != active
    assert execution[1].parent == (tmp_path / "external-runs").resolve()
    assert (execution[1] / AGY_INVOCATION_MARKER).is_file()


def test_expected_folder_missing_does_not_trust_stdout_or_search_alternatives(tmp_path: Path) -> None:
    runner, fake, _, active, scratch, state, _ = fixture(tmp_path, mode="missing")
    before = (active / "ACTIVE.txt").read_bytes()
    result = runner.run()
    assert result.classification == C.EXPECTED_FOLDER_MISSING
    assert not result.expected_folder_found and not result.review_created
    assert result.manual_import_fallback_available
    assert (scratch / "unrelated_alternative").is_dir()
    assert not (state / "bridge-reviews.jsonl").exists()
    assert (active / "ACTIVE.txt").read_bytes() == before
    persisted = (state / "agy-assisted-runner-status.json").read_text(encoding="utf-8")
    assert str(scratch) not in persisted and "created " not in persisted


@pytest.mark.parametrize(
    ("mode", "classification"),
    [("secret", C.SECRET_FILE_BLOCKED), ("binary", C.BINARY_FILE_BLOCKED), ("cwd_write", C.SOURCE_UNSAFE)],
)
def test_unsafe_generated_output_is_classified_without_review(tmp_path: Path, mode: str, classification: str) -> None:
    runner, _, _, _, _, state, _ = fixture(tmp_path, mode=mode)
    result = runner.run()
    assert result.classification == classification
    assert not result.review_created
    assert not (state / "bridge-reviews.jsonl").exists()


def test_expected_symlink_is_blocked_when_supported(tmp_path: Path) -> None:
    runner, _, *_ = fixture(tmp_path, mode="expected_symlink")
    try:
        result = runner.run()
    except OSError:
        pytest.skip("host does not permit links")
    assert result.classification in {C.UNSAFE_ABORTED, C.SYMLINK_BLOCKED}
    assert not result.review_created


def test_provider_error_and_timeout_are_distinct(tmp_path: Path) -> None:
    error_runner, *_ = fixture(tmp_path / "error", mode="provider_error")
    timeout_runner, *_ = fixture(tmp_path / "timeout", mode="timeout")
    error = error_runner.run()
    timeout = timeout_runner.run()
    assert error.classification == C.PROVIDER_ERROR and error.manual_import_fallback_available
    assert timeout.classification == C.TIMEOUT and timeout.manual_import_fallback_available


def test_valid_expected_folder_imports_and_creates_hardened_review(tmp_path: Path) -> None:
    runner, fake, _, active, _, state, reviews = fixture(tmp_path)
    before = (active / "ACTIVE.txt").read_bytes()
    result = runner.run()
    assert result.classification == C.PASS
    assert result.expected_folder_found and result.file_count == 3
    assert result.project_type == "PlatformIO"
    assert result.review_created and result.review_id
    assert result.active_workspace_unchanged
    review = reviews.get_review(result.review_id)
    assert review.provider_id == "agy_scratch_runner"
    assert review.artifact_metadata["template"] == SUPPORTED_TEMPLATE
    assert review.artifact_metadata["source"] == "expected_agy_scratch_folder"
    assert review.artifact_metadata["project_type"] == "PlatformIO"
    assert review.artifact_metadata["risk_warnings"]
    assert (active / "ACTIVE.txt").read_bytes() == before
    persisted = "\n".join(path.read_text(encoding="utf-8") for path in state.glob("*.json*"))
    assert fake.raw_instruction not in persisted
    assert "raw output must not persist" not in persisted
    assert "raw error must not persist" not in persisted


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["platformio.ini", "src/main.cpp"], "PlatformIO"),
        (["blink.ino"], "Arduino"),
        (["CMakeLists.txt", "main/CMakeLists.txt", "main/main.c"], "ESP-IDF"),
        (["main.py"], "MicroPython"),
    ],
)
def test_project_type_detection(paths: list[str], expected: str) -> None:
    files = tuple(ValidatedFile(path, b"text") for path in paths)
    assert detect_project_type(files) == expected


def test_runtime_source_does_not_persist_raw_process_data_or_scan_scratch() -> None:
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "stdout" not in source[source.index("def _finish"):source.index("def runtime_instruction")]
    assert "stderr" not in source[source.index("def _finish"):source.index("def runtime_instruction")]
    assert "iterdir" not in source and "glob(" not in source and "rglob(" not in source
    assert "newest" not in source and "last-modified" not in source


def test_safe_environment_matches_vetted_agy_runtime_without_secrets() -> None:
    value = safe_environment({
        "PATH": "safe", "COMSPEC": "cmd.exe", "TMPDIR": "tmp", "LANG": "en_US",
        "OPENAI_API_KEY": "secret", "AGY_TOKEN": "secret",
    })
    assert value == {"PATH": "safe", "COMSPEC": "cmd.exe", "TMPDIR": "tmp", "LANG": "en_US"}
