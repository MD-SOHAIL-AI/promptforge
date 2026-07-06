"""Manual, exact-path AGY scratch project import with no provider execution."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from .diff_service import BridgeDiffService, is_safe_relative_path


MAX_FILES = 200
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_DEPTH = 8
MARKER_NAME = "README_FORGEX_AGY_IMPORT_SANDBOX.txt"
MARKER_CONTENT = "ForgeX managed AGY scratch project import sandbox.\n"
ALLOWED_EXTENSIONS = frozenset({
    ".c", ".cpp", ".h", ".hpp", ".ino", ".py", ".txt", ".md", ".ini",
    ".json", ".yml", ".yaml", ".toml", ".cmake",
})
ALLOWED_FILENAMES = frozenset({"cmakelists.txt", "platformio.ini", "readme.md", "sdkconfig.defaults"})
REJECTED_DIRECTORIES = frozenset({
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", ".promptforge", ".gemini", ".codex", ".ssh",
})
SECRET_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa",
    "id_ed25519", "auth.json", "credentials.json", "token.json", "secrets.*",
)
WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


class AGYScratchImportClassification:
    PASS = "AGY_SCRATCH_IMPORT_PASS"
    SOURCE_MISSING = "AGY_SCRATCH_SOURCE_MISSING"
    SOURCE_OUTSIDE_ROOT = "AGY_SCRATCH_SOURCE_OUTSIDE_ROOT"
    SOURCE_IS_ROOT = "AGY_SCRATCH_SOURCE_IS_ROOT"
    SOURCE_UNSAFE_PATH = "AGY_SCRATCH_SOURCE_UNSAFE_PATH"
    SYMLINK_BLOCKED = "AGY_SCRATCH_SYMLINK_BLOCKED"
    TOO_MANY_FILES = "AGY_SCRATCH_TOO_MANY_FILES"
    TOO_LARGE = "AGY_SCRATCH_TOO_LARGE"
    FILE_TOO_LARGE = "AGY_SCRATCH_FILE_TOO_LARGE"
    UNSUPPORTED_FILE_TYPE = "AGY_SCRATCH_UNSUPPORTED_FILE_TYPE"
    SECRET_FILE_BLOCKED = "AGY_SCRATCH_SECRET_FILE_BLOCKED"
    BINARY_FILE_BLOCKED = "AGY_SCRATCH_BINARY_FILE_BLOCKED"
    ACTIVE_WORKSPACE_UNSAFE = "AGY_SCRATCH_ACTIVE_WORKSPACE_UNSAFE"
    REVIEW_CREATE_FAILED = "AGY_SCRATCH_REVIEW_CREATE_FAILED"
    UNKNOWN_SAFE_FAILURE = "AGY_SCRATCH_UNKNOWN_SAFE_FAILURE"


class AGYScratchImportError(ValueError):
    def __init__(
        self,
        classification: str,
        *,
        source_name: str = "unknown",
        source_inside_scratch_root: bool = False,
        file_count: int = 0,
        total_bytes: int = 0,
        blocked_files_count: int = 0,
    ) -> None:
        super().__init__(classification)
        self.classification = classification
        self.source_name = safe_source_name(source_name)
        self.source_inside_scratch_root = source_inside_scratch_root
        self.file_count = file_count
        self.total_bytes = total_bytes
        self.blocked_files_count = blocked_files_count

    def to_result(self) -> "AGYScratchImportResult":
        return AGYScratchImportResult(
            classification=self.classification,
            source_inside_scratch_root=self.source_inside_scratch_root,
            source_name=self.source_name,
            file_count=self.file_count,
            total_bytes=self.total_bytes,
            blocked_files_count=self.blocked_files_count,
            managed_sandbox_created=False,
            created_file_count=0,
            modified_file_count=0,
            deleted_file_count=0,
            review_created=False,
            review_id=None,
            active_workspace_unchanged=True,
        )


@dataclass(frozen=True, slots=True)
class ValidatedFile:
    relative_path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ValidatedProject:
    source_root: Path
    source_name: str
    files: tuple[ValidatedFile, ...]
    total_bytes: int


@dataclass(frozen=True, slots=True)
class ImportReviewContext:
    provider_id: str = "agy_scratch_import"
    source: str = "manual_agy_scratch_folder"
    execution_mode: str = "manual_import"
    workspace_mode: str = "managed_import_sandbox"
    classification: str = AGYScratchImportClassification.PASS
    template: str | None = None
    run_id: str | None = None
    expected_source_name: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AGYScratchImportResult:
    classification: str
    source_inside_scratch_root: bool
    source_name: str
    file_count: int
    total_bytes: int
    blocked_files_count: int
    managed_sandbox_created: bool
    created_file_count: int
    modified_file_count: int
    deleted_file_count: int
    review_created: bool
    review_id: str | None
    active_workspace_unchanged: bool
    project_type: str = "Unknown"
    warnings: tuple[str, ...] = ()

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "classification": self.classification,
            "source_inside_scratch_root": self.source_inside_scratch_root,
            "source_name": self.source_name,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "blocked_files_count": self.blocked_files_count,
            "managed_sandbox_created": self.managed_sandbox_created,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "review_created": self.review_created,
            "review_id": self.review_id,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "project_type": self.project_type,
            "warnings": list(self.warnings),
            "auto_apply": False,
            "auto_build": False,
            "auto_flash": False,
        }


def default_agy_scratch_root() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "scratch"


class AGYScratchProjectImportService:
    """Validate and copy one user-selected AGY scratch project into ForgeX state."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        active_workspace_root: str | Path,
        managed_sandbox_root: str | Path,
        review_service: BridgeDiffService,
        scratch_root: str | Path | None = None,
        status_path: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.active_workspace_root = Path(active_workspace_root).resolve()
        self.managed_sandbox_root = Path(managed_sandbox_root).resolve()
        self.review_service = review_service
        self.scratch_root = Path(scratch_root) if scratch_root is not None else default_agy_scratch_root()
        self.status_path = Path(status_path) if status_path is not None else None
        self.env = dict(os.environ if env is None else env)

    def import_project(self, source_path: str, *, review_context: ImportReviewContext | None = None) -> AGYScratchImportResult:
        context = review_context or ImportReviewContext()
        try:
            project = self.validate_source(source_path)
            project_type = detect_project_type(project.files)
            active_before = snapshot_integrity(self.active_workspace_root)
            sandbox = self._create_sandbox()
            baseline = self.review_service.inspect_workspace(sandbox)
            for item in project.files:
                target = safe_target(sandbox, item.relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item.content)
            active_unchanged = snapshot_integrity(self.active_workspace_root) == active_before
            if not active_unchanged:
                raise AGYScratchImportError(
                    AGYScratchImportClassification.ACTIVE_WORKSPACE_UNSAFE,
                    source_name=project.source_name,
                    source_inside_scratch_root=True,
                    file_count=len(project.files),
                    total_bytes=project.total_bytes,
                )
            if (sandbox / MARKER_NAME).read_text(encoding="utf-8") != MARKER_CONTENT:
                raise AGYScratchImportError(AGYScratchImportClassification.UNKNOWN_SAFE_FAILURE)
            changed = self.review_service.diff_snapshot(baseline, sandbox)
            exact = (
                len(changed) == len(project.files)
                and all(item.change_type == "created" and item.safe for item in changed)
                and {item.path for item in changed} == {item.relative_path for item in project.files}
            )
            if not exact:
                raise AGYScratchImportError(AGYScratchImportClassification.REVIEW_CREATE_FAILED)
            try:
                review = self.review_service.create_review(
                    provider_id=context.provider_id,
                    workspace_root=sandbox,
                    snapshot=baseline,
                    artifact_source=context.source,
                    artifact_type="scratch_project_import",
                    artifact_metadata={
                        "source": context.source,
                        "source_name": project.source_name,
                        "execution_mode": context.execution_mode,
                        "workspace_mode": context.workspace_mode,
                        "classification": context.classification,
                        "template": context.template,
                        "run_id": context.run_id,
                        "expected_source_name": context.expected_source_name,
                        "file_tree": [item.relative_path for item in project.files],
                        "total_bytes": project.total_bytes,
                        "project_type": project_type,
                        "risk_warnings": list(context.warnings),
                        "created_file_count": len(project.files),
                        "modified_file_count": 0,
                        "deleted_file_count": 0,
                        "active_workspace_unchanged": True,
                        "auto_apply": False,
                        "auto_build": False,
                        "auto_flash": False,
                    },
                )
            except Exception as exc:
                raise AGYScratchImportError(
                    AGYScratchImportClassification.REVIEW_CREATE_FAILED,
                    source_name=project.source_name,
                    source_inside_scratch_root=True,
                    file_count=len(project.files),
                    total_bytes=project.total_bytes,
                ) from exc
            result = AGYScratchImportResult(
                classification=AGYScratchImportClassification.PASS,
                source_inside_scratch_root=True,
                source_name=project.source_name,
                file_count=len(project.files),
                total_bytes=project.total_bytes,
                blocked_files_count=0,
                managed_sandbox_created=True,
                created_file_count=len(project.files),
                modified_file_count=0,
                deleted_file_count=0,
                review_created=True,
                review_id=review.review_id,
                active_workspace_unchanged=True,
                project_type=project_type,
                warnings=context.warnings,
            )
            self._persist_status(result)
            return result
        except AGYScratchImportError as exc:
            self._persist_status(exc.to_result())
            raise
        except Exception as exc:
            error = AGYScratchImportError(AGYScratchImportClassification.UNKNOWN_SAFE_FAILURE)
            self._persist_status(error.to_result())
            raise error from exc

    def validate_source(self, source_path: str) -> ValidatedProject:
        if not isinstance(source_path, str) or not source_path.strip():
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_MISSING)
        raw = Path(source_path.strip())
        source_name = safe_source_name(raw.name)
        if not raw.is_absolute() or ".." in raw.parts or PureWindowsPath(source_path).drive and not PureWindowsPath(source_path).is_absolute():
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name)

        scratch = self.scratch_root.expanduser()
        lexical_source = Path(os.path.abspath(raw))
        lexical_scratch = Path(os.path.abspath(scratch))
        if self._is_brain_path(lexical_source):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name)
        if same_path(lexical_source, lexical_scratch):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_IS_ROOT, source_name=source_name, source_inside_scratch_root=True)
        if not is_strict_child(lexical_scratch, lexical_source):
            classification = AGYScratchImportClassification.SOURCE_UNSAFE_PATH if self._is_sensitive_exact(lexical_source) else AGYScratchImportClassification.SOURCE_OUTSIDE_ROOT
            raise AGYScratchImportError(classification, source_name=source_name)
        if not lexical_source.exists() or not lexical_source.is_dir():
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_MISSING, source_name=source_name, source_inside_scratch_root=True)
        if is_link_or_reparse(lexical_source):
            raise AGYScratchImportError(AGYScratchImportClassification.SYMLINK_BLOCKED, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)
        if not lexical_scratch.exists() or not lexical_scratch.is_dir() or is_link_or_reparse(lexical_scratch):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name)
        real_scratch = lexical_scratch.resolve(strict=True)
        real_source = lexical_source.resolve(strict=True)
        if not is_strict_child(real_scratch, real_source):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_OUTSIDE_ROOT, source_name=source_name)
        if self._is_brain_path(real_source) or self._is_sensitive_exact(real_source):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name)

        files: list[ValidatedFile] = []
        total_bytes = 0
        stack: list[tuple[Path, int]] = [(real_source, 0)]
        while stack:
            current, directory_depth = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError as exc:
                raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name) from exc
            for entry in entries:
                entry_path = Path(entry.path)
                relative = entry_path.relative_to(real_source).as_posix()
                depth = len(Path(relative).parts)
                if depth > MAX_DEPTH:
                    raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)
                if is_link_or_reparse(entry_path):
                    raise AGYScratchImportError(AGYScratchImportClassification.SYMLINK_BLOCKED, source_name=source_name, source_inside_scratch_root=True, file_count=len(files), total_bytes=total_bytes, blocked_files_count=1)
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.casefold() in REJECTED_DIRECTORIES or entry.name.casefold() == "brain":
                        raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name, source_inside_scratch_root=True, file_count=len(files), total_bytes=total_bytes, blocked_files_count=1)
                    stack.append((entry_path, directory_depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False) or not is_safe_relative_path(relative):
                    raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)
                validate_project_filename(entry.name, source_name)
                size = entry.stat(follow_symlinks=False).st_size
                if size > MAX_FILE_BYTES:
                    raise AGYScratchImportError(AGYScratchImportClassification.FILE_TOO_LARGE, source_name=source_name, source_inside_scratch_root=True, file_count=len(files), total_bytes=total_bytes, blocked_files_count=1)
                if len(files) + 1 > MAX_FILES:
                    raise AGYScratchImportError(AGYScratchImportClassification.TOO_MANY_FILES, source_name=source_name, source_inside_scratch_root=True, file_count=len(files) + 1, total_bytes=total_bytes, blocked_files_count=1)
                total_bytes += size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise AGYScratchImportError(AGYScratchImportClassification.TOO_LARGE, source_name=source_name, source_inside_scratch_root=True, file_count=len(files) + 1, total_bytes=total_bytes, blocked_files_count=1)
                content = entry_path.read_bytes()
                if len(content) != size or is_binary(content):
                    raise AGYScratchImportError(AGYScratchImportClassification.BINARY_FILE_BLOCKED, source_name=source_name, source_inside_scratch_root=True, file_count=len(files), total_bytes=total_bytes, blocked_files_count=1)
                files.append(ValidatedFile(relative, content))
        if not files:
            raise AGYScratchImportError(AGYScratchImportClassification.UNKNOWN_SAFE_FAILURE, source_name=source_name, source_inside_scratch_root=True)
        files.sort(key=lambda item: item.relative_path.casefold())
        return ValidatedProject(real_source, source_name, tuple(files), total_bytes)

    def _create_sandbox(self) -> Path:
        expected_base = (self.repository_root / ".promptforge" / "agy-import-sandboxes").resolve()
        if not same_path(self.managed_sandbox_root, expected_base):
            raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH)
        self.managed_sandbox_root.mkdir(parents=True, exist_ok=True)
        if is_link_or_reparse(self.managed_sandbox_root):
            raise AGYScratchImportError(AGYScratchImportClassification.SYMLINK_BLOCKED)
        run_id = f"agy-import-{uuid.uuid4().hex}"
        sandbox = self.managed_sandbox_root / run_id
        sandbox.mkdir(parents=False, exist_ok=False)
        (sandbox / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
        return sandbox

    def _is_sensitive_exact(self, candidate: Path) -> bool:
        home = Path.home().resolve()
        roots = [home, home / "Desktop", self.repository_root, self.active_workspace_root]
        roots.extend(Path(value).resolve() for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial") if (value := self.env.get(key)))
        return any(same_path(candidate, root) for root in roots)

    @staticmethod
    def _is_brain_path(candidate: Path) -> bool:
        parts = [part.casefold() for part in candidate.parts]
        return "brain" in parts and ".gemini" in parts

    def _persist_status(self, result: AGYScratchImportResult) -> None:
        if self.status_path is None:
            return
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        self.status_path.write_text(json.dumps(result.to_safe_dict(), ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")


def validate_project_filename(filename: str, source_name: str) -> None:
    lowered = filename.casefold()
    if any(fnmatch.fnmatchcase(lowered, pattern) for pattern in SECRET_PATTERNS):
        raise AGYScratchImportError(AGYScratchImportClassification.SECRET_FILE_BLOCKED, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)
    if lowered.rstrip(". ").split(".", 1)[0] in WINDOWS_RESERVED_NAMES:
        raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)
    if lowered not in ALLOWED_FILENAMES and Path(lowered).suffix not in ALLOWED_EXTENSIONS:
        raise AGYScratchImportError(AGYScratchImportClassification.UNSUPPORTED_FILE_TYPE, source_name=source_name, source_inside_scratch_root=True, blocked_files_count=1)


def detect_project_type(files: tuple[ValidatedFile, ...]) -> str:
    names = {item.relative_path.replace("\\", "/").casefold() for item in files}
    basenames = {Path(name).name.casefold() for name in names}
    if "platformio.ini" in basenames:
        return "PlatformIO"
    if any(name.endswith(".ino") for name in names):
        return "Arduino"
    if "cmakelists.txt" in names and "main/cmakelists.txt" in names:
        return "ESP-IDF"
    if "main.py" in names:
        return "MicroPython"
    return "Unknown"


def is_binary(content: bytes) -> bool:
    if b"\x00" in content:
        return True
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return any(ord(char) < 32 and char not in "\t\n\r" for char in text)


def safe_target(sandbox: Path, relative_path: str) -> Path:
    if not is_safe_relative_path(relative_path):
        raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH)
    target = (sandbox / relative_path).resolve()
    if not is_strict_child(sandbox.resolve(), target):
        raise AGYScratchImportError(AGYScratchImportClassification.SOURCE_UNSAFE_PATH)
    return target


def snapshot_integrity(root: Path) -> tuple[tuple[str, str], ...]:
    if not root.exists() or not root.is_dir() or is_link_or_reparse(root):
        raise AGYScratchImportError(AGYScratchImportClassification.ACTIVE_WORKSPACE_UNSAFE)
    records: list[tuple[str, str]] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(dirs):
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if is_link_or_reparse(child):
                records.append((relative, f"link:{os.readlink(child)}"))
        for name in sorted(files):
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if is_link_or_reparse(child):
                records.append((relative, f"link:{os.readlink(child)}"))
            else:
                records.append((relative, hashlib.sha256(child.read_bytes()).hexdigest()))
    return tuple(sorted(records))


def is_link_or_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return False
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def is_strict_child(parent: Path, child: Path) -> bool:
    try:
        relative = child.relative_to(parent)
    except ValueError:
        return False
    return bool(relative.parts)


def same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def safe_source_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")[:128]
    return cleaned or "unknown"
