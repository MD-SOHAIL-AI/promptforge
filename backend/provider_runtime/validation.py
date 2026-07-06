"""Content-based generated artifact validation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping

from .contracts import WorkspaceMode


PROTECTED_PARTS = frozenset({".git", ".pio", ".promptforge", ".forgex", "node_modules", "build", "dist"})


def safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    candidate = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        not normalized
        or candidate.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or any(part.casefold() in PROTECTED_PARTS for part in candidate.parts)
    ):
        raise ValueError("INVALID_GENERATED_OUTPUT")
    return candidate.as_posix()


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactValidationResult:
    valid: bool
    created_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    unchanged_files: tuple[str, ...] = ()
    missing_required_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def changed_files(self) -> tuple[str, ...]:
        return (*self.created_files, *self.modified_files, *self.deleted_files)

    @property
    def no_op_success(self) -> bool:
        return self.valid and not self.changed_files and bool(self.unchanged_files)


class GeneratedArtifactValidator:
    def validate(
        self,
        workspace_root: str | Path,
        generated_files: Mapping[str, str | bytes | None],
        *,
        required_files: tuple[str, ...] = (),
        allow_deletions: bool = False,
    ) -> ArtifactValidationResult:
        root = Path(workspace_root).resolve(strict=True)
        created: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []
        unchanged: list[str] = []
        errors: list[str] = []
        normalized: dict[str, str | bytes | None] = {}
        for raw_path, content in generated_files.items():
            try:
                path = safe_relative_path(raw_path)
            except ValueError:
                errors.append(f"unsafe_path:{raw_path}")
                continue
            if content is not None and not isinstance(content, (str, bytes)):
                errors.append(f"invalid_content:{path}")
                continue
            target = (root / path).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"unsafe_path:{path}")
                continue
            normalized[path] = content
            if content is None:
                if not allow_deletions:
                    errors.append(f"deletion_not_allowed:{path}")
                elif target.exists():
                    deleted.append(path)
                else:
                    unchanged.append(path)
                continue
            payload = content.encode("utf-8") if isinstance(content, str) else content
            if not payload:
                errors.append(f"empty_output:{path}")
                continue
            if not target.exists():
                created.append(path)
            elif not target.is_file() or target.is_symlink():
                errors.append(f"invalid_target:{path}")
            elif content_hash(target.read_bytes()) == content_hash(payload):
                unchanged.append(path)
            else:
                modified.append(path)
        required = []
        for item in required_files:
            try:
                required.append(safe_relative_path(item))
            except ValueError:
                errors.append(f"unsafe_required_path:{item}")
        missing = tuple(sorted(path for path in required if path not in normalized and not (root / path).is_file()))
        if missing:
            errors.extend(f"missing_required:{path}" for path in missing)
        if not normalized:
            errors.append("invalid_empty_output")
        return ArtifactValidationResult(
            valid=not errors,
            created_files=tuple(sorted(created)),
            modified_files=tuple(sorted(modified)),
            deleted_files=tuple(sorted(deleted)),
            unchanged_files=tuple(sorted(unchanged)),
            missing_required_files=missing,
            errors=tuple(errors),
        )


def detect_workspace_mode(workspace_root: str | Path) -> WorkspaceMode:
    root = Path(workspace_root).resolve(strict=True)
    return WorkspaceMode.MODIFY_EXISTING if (root / "platformio.ini").is_file() else WorkspaceMode.GENERATE_INTO_FOLDER
