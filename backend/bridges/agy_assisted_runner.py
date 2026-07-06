"""Guarded AGY scratch generator that imports one precomputed expected folder."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .agy_scratch_project_import import (
    AGYScratchImportClassification,
    AGYScratchImportError,
    AGYScratchProjectImportService,
    ImportReviewContext,
    is_link_or_reparse,
    snapshot_integrity,
)


AGY_INVOCATION_ROOT = Path(r"C:\forgex-agy-runs")
AGY_INVOCATION_MARKER = "README_FORGEX_AGY_RUN.txt"
AGY_INVOCATION_MARKER_CONTENT = "ForgeX disposable AGY assisted-generation invocation workspace.\n"
SUPPORTED_TEMPLATE = "esp32-platformio-blink"
ASSISTED_TIMEOUT_SECONDS = 300


class AGYAssistedClassification:
    PASS = "AGY_ASSISTED_IMPORT_PASS"
    EXPECTED_FOLDER_MISSING = "AGY_ASSISTED_EXPECTED_FOLDER_MISSING"
    SOURCE_OUTSIDE_ROOT = "AGY_ASSISTED_SOURCE_OUTSIDE_ROOT"
    SOURCE_UNSAFE = "AGY_ASSISTED_SOURCE_UNSAFE"
    SYMLINK_BLOCKED = "AGY_ASSISTED_SYMLINK_BLOCKED"
    TOO_MANY_FILES = "AGY_ASSISTED_TOO_MANY_FILES"
    TOO_LARGE = "AGY_ASSISTED_TOO_LARGE"
    SECRET_FILE_BLOCKED = "AGY_ASSISTED_SECRET_FILE_BLOCKED"
    BINARY_FILE_BLOCKED = "AGY_ASSISTED_BINARY_FILE_BLOCKED"
    PROVIDER_ERROR = "AGY_ASSISTED_PROVIDER_ERROR"
    TIMEOUT = "AGY_ASSISTED_TIMEOUT"
    UNSAFE_ABORTED = "AGY_ASSISTED_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "AGY_ASSISTED_UNKNOWN_SAFE_FAILURE"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


ProcessRunner = Callable[[Sequence[str], Path, int, dict[str, str]], ProcessResult]
ExecutableResolver = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class AGYAssistedResult:
    classification: str
    agy_found: bool
    agy_version: str | None
    run_id: str
    expected_folder_name: str
    expected_folder_found: bool
    file_count: int
    total_bytes: int
    project_type: str
    review_created: bool
    review_id: str | None
    active_workspace_unchanged: bool
    manual_import_fallback_available: bool

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "agy_found": self.agy_found,
            "agy_version": self.agy_version,
            "instruction_type": "agy_esp32_platformio_project",
            "run_id": self.run_id,
            "expected_folder_name": self.expected_folder_name,
            "expected_source_name": self.expected_folder_name,
            "expected_folder_found": self.expected_folder_found,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "project_type": self.project_type,
            "review_created": self.review_created,
            "review_id": self.review_id,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "manual_import_fallback_available": self.manual_import_fallback_available,
            "auto_apply": False,
            "auto_build": False,
            "auto_flash": False,
        }


class AGYAssistedRunner:
    def __init__(
        self,
        *,
        repository_root: str | Path,
        active_workspace_root: str | Path,
        import_service: AGYScratchProjectImportService,
        invocation_root: str | Path = AGY_INVOCATION_ROOT,
        feature_enabled: bool = False,
        process_runner: ProcessRunner | None = None,
        executable_resolver: ExecutableResolver | None = None,
        status_path: str | Path | None = None,
        env: dict[str, str] | None = None,
        home_root: str | Path | None = None,
        timeout_seconds: int = ASSISTED_TIMEOUT_SECONDS,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.active_workspace_root = Path(active_workspace_root).resolve()
        self.import_service = import_service
        self.invocation_root = Path(invocation_root).resolve()
        self.feature_enabled = feature_enabled
        self.process_runner = process_runner or run_process
        self.executable_resolver = executable_resolver or shutil.which
        self.status_path = Path(status_path) if status_path is not None else None
        self.env = dict(os.environ if env is None else env)
        self.home_root = Path(home_root).resolve() if home_root is not None else Path.home().resolve()
        self.timeout_seconds = timeout_seconds

    def run(self, template: str = SUPPORTED_TEMPLATE) -> AGYAssistedResult:
        run_id = f"agy-{uuid.uuid4().hex}"
        nonce = uuid.uuid4().hex[:16]
        expected_name = f"forgex_agy_{run_id}_{nonce}"
        base = self._empty_result(AGYAssistedClassification.UNSAFE_ABORTED, run_id, expected_name)
        if not self.feature_enabled or template != SUPPORTED_TEMPLATE:
            return self._finish(base)
        executable = self.executable_resolver("agy")
        if not executable:
            return self._finish(self._replace(base, classification=AGYAssistedClassification.PROVIDER_ERROR))
        try:
            workspace = self._create_invocation_workspace(run_id)
            active_before = snapshot_integrity(self.active_workspace_root)
            invocation_before = snapshot_integrity(workspace)
            expected_path = self.import_service.scratch_root / expected_name
            if expected_path.exists() or is_link_or_reparse(expected_path):
                return self._finish(self._replace(base, classification=AGYAssistedClassification.UNSAFE_ABORTED, agy_found=True))
            version_result = self.process_runner([executable, "--version"], workspace, 10, safe_environment(self.env))
            version = sanitize_version(version_result.stdout) if version_result.returncode == 0 else None
            instruction = runtime_instruction(expected_name)
            process = self.process_runner([executable, "-p", instruction], workspace, self.timeout_seconds, safe_environment(self.env))
            if snapshot_integrity(self.active_workspace_root) != active_before:
                return self._finish(self._replace(base, classification=AGYAssistedClassification.UNSAFE_ABORTED, agy_found=True, agy_version=version, active_workspace_unchanged=False))
            if snapshot_integrity(workspace) != invocation_before:
                return self._finish(self._replace(base, classification=AGYAssistedClassification.SOURCE_UNSAFE, agy_found=True, agy_version=version))
            if process.returncode != 0:
                return self._finish(self._replace(
                    base,
                    classification=AGYAssistedClassification.PROVIDER_ERROR,
                    agy_found=True,
                    agy_version=version,
                    manual_import_fallback_available=True,
                ))
            if not expected_path.exists():
                return self._finish(self._replace(
                    base,
                    classification=AGYAssistedClassification.EXPECTED_FOLDER_MISSING,
                    agy_found=True,
                    agy_version=version,
                    manual_import_fallback_available=True,
                ))
            if not expected_path.is_dir():
                return self._finish(self._replace(base, classification=AGYAssistedClassification.SOURCE_UNSAFE, agy_found=True, agy_version=version, expected_folder_found=True))
            context = ImportReviewContext(
                provider_id="agy_scratch_runner",
                source="expected_agy_scratch_folder",
                execution_mode="assisted_scratch_generation",
                workspace_mode="agy_scratch_import",
                classification=AGYAssistedClassification.PASS,
                template=SUPPORTED_TEMPLATE,
                run_id=run_id,
                expected_source_name=expected_name,
                warnings=("Generated code requires manual review before apply.",),
            )
            imported = self.import_service.import_project(str(expected_path), review_context=context)
            result = AGYAssistedResult(
                classification=AGYAssistedClassification.PASS,
                agy_found=True,
                agy_version=version,
                run_id=run_id,
                expected_folder_name=expected_name,
                expected_folder_found=True,
                file_count=imported.file_count,
                total_bytes=imported.total_bytes,
                project_type=imported.project_type,
                review_created=imported.review_created,
                review_id=imported.review_id,
                active_workspace_unchanged=imported.active_workspace_unchanged,
                manual_import_fallback_available=True,
            )
            return self._finish(result)
        except subprocess.TimeoutExpired:
            return self._finish(self._replace(
                base,
                classification=AGYAssistedClassification.TIMEOUT,
                agy_found=True,
                manual_import_fallback_available=True,
            ))
        except AGYScratchImportError as exc:
            return self._finish(self._replace(
                base,
                classification=map_import_classification(exc.classification),
                agy_found=True,
                expected_folder_found=True,
                file_count=exc.file_count,
                total_bytes=exc.total_bytes,
                manual_import_fallback_available=True,
            ))
        except (OSError, ValueError):
            return self._finish(self._replace(base, classification=AGYAssistedClassification.UNSAFE_ABORTED, agy_found=True))
        except Exception:
            return self._finish(self._replace(base, classification=AGYAssistedClassification.UNKNOWN_SAFE_FAILURE, agy_found=True))

    def _create_invocation_workspace(self, run_id: str) -> Path:
        self._guard_invocation_root(self.invocation_root)
        self.invocation_root.mkdir(parents=True, exist_ok=True)
        self._guard_invocation_root(self.invocation_root)
        workspace = self.invocation_root / run_id
        workspace.mkdir(parents=False, exist_ok=False)
        (workspace / AGY_INVOCATION_MARKER).write_text(AGY_INVOCATION_MARKER_CONTENT, encoding="utf-8")
        if is_link_or_reparse(workspace):
            raise ValueError("agy_invocation_workspace_linked")
        return workspace

    def _guard_invocation_root(self, root: Path) -> None:
        if root.exists() and (not root.is_dir() or is_link_or_reparse(root)):
            raise ValueError("agy_invocation_root_invalid")
        if same_path(root, Path(root.anchor)) or paths_overlap(root, self.repository_root) or paths_overlap(root, self.active_workspace_root):
            raise ValueError("agy_invocation_root_unsafe")
        sensitive = [self.home_root, self.home_root / "Desktop"]
        sensitive.extend(Path(value).resolve() for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if (value := self.env.get(key)))
        if any(same_path(root, value) or is_child(value, root) for value in sensitive):
            raise ValueError("agy_invocation_root_sensitive")

    def _finish(self, result: AGYAssistedResult) -> AGYAssistedResult:
        if self.status_path is not None:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            self.status_path.write_text(json.dumps(result.to_safe_dict(), ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
        return result

    @staticmethod
    def _replace(value: AGYAssistedResult, **updates: object) -> AGYAssistedResult:
        fields = value.to_safe_dict()
        fields.update(updates)
        return AGYAssistedResult(
            classification=str(fields["classification"]),
            agy_found=bool(fields["agy_found"]),
            agy_version=fields["agy_version"] if isinstance(fields["agy_version"], str) else None,
            run_id=str(fields["run_id"]),
            expected_folder_name=str(fields["expected_folder_name"]),
            expected_folder_found=bool(fields["expected_folder_found"]),
            file_count=int(fields["file_count"]),
            total_bytes=int(fields["total_bytes"]),
            project_type=str(fields["project_type"]),
            review_created=bool(fields["review_created"]),
            review_id=fields["review_id"] if isinstance(fields["review_id"], str) else None,
            active_workspace_unchanged=bool(fields["active_workspace_unchanged"]),
            manual_import_fallback_available=bool(fields["manual_import_fallback_available"]),
        )

    @staticmethod
    def _empty_result(classification: str, run_id: str, expected_name: str) -> AGYAssistedResult:
        return AGYAssistedResult(classification, False, None, run_id, expected_name, False, 0, 0, "Unknown", False, None, True, False)


def runtime_instruction(expected_folder_name: str) -> str:
    if not re.fullmatch(r"forgex_agy_agy-[a-f0-9]{32}_[a-f0-9]{16}", expected_folder_name):
        raise ValueError("agy_expected_folder_name_invalid")
    return "\n".join((
        "Create an ESP32 starter project in the Antigravity CLI scratch directory.",
        "", "Create exactly one project folder named:", expected_folder_name, "",
        "Inside that folder, create a PlatformIO ESP32 blink project with:",
        "- platformio.ini", "- src/main.cpp", "- README.md", "",
        "Use GPIO 2 as the default LED pin.",
        "Do not create files outside that project folder.",
        "Do not create files in the current working directory.",
        "Do not modify existing files.", "Do not access the network.",
        "Do not install dependencies.",
    ))


def run_process(args: Sequence[str], cwd: Path, timeout: int, env: dict[str, str]) -> ProcessResult:
    completed = subprocess.run(
        list(args), cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, shell=False, timeout=timeout, env=env,
        check=False,
    )
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def safe_environment(env: dict[str, str]) -> dict[str, str]:
    allowed = (
        "PATH", "Path", "PATHEXT", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC",
        "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
        "LANG", "LC_ALL",
    )
    return {name: env[name] for name in allowed if isinstance(env.get(name), str)}


def sanitize_version(value: str) -> str | None:
    version = value.strip()
    return version if re.fullmatch(r"[ -~]{1,80}", version) else None


def map_import_classification(value: str) -> str:
    return {
        AGYScratchImportClassification.SOURCE_OUTSIDE_ROOT: AGYAssistedClassification.SOURCE_OUTSIDE_ROOT,
        AGYScratchImportClassification.SOURCE_IS_ROOT: AGYAssistedClassification.SOURCE_UNSAFE,
        AGYScratchImportClassification.SOURCE_UNSAFE_PATH: AGYAssistedClassification.SOURCE_UNSAFE,
        AGYScratchImportClassification.SYMLINK_BLOCKED: AGYAssistedClassification.SYMLINK_BLOCKED,
        AGYScratchImportClassification.TOO_MANY_FILES: AGYAssistedClassification.TOO_MANY_FILES,
        AGYScratchImportClassification.TOO_LARGE: AGYAssistedClassification.TOO_LARGE,
        AGYScratchImportClassification.FILE_TOO_LARGE: AGYAssistedClassification.TOO_LARGE,
        AGYScratchImportClassification.SECRET_FILE_BLOCKED: AGYAssistedClassification.SECRET_FILE_BLOCKED,
        AGYScratchImportClassification.BINARY_FILE_BLOCKED: AGYAssistedClassification.BINARY_FILE_BLOCKED,
    }.get(value, AGYAssistedClassification.UNKNOWN_SAFE_FAILURE)


def paths_overlap(first: Path, second: Path) -> bool:
    return same_path(first, second) or is_child(first, second) or is_child(second, first)


def is_child(parent: Path, child: Path) -> bool:
    try:
        relative = child.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts)


def same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))
