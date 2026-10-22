"""Isolated, auditable PlatformIO source capture.

A compiler must not observe subsequent edits to a live user workspace. This
module deliberately copies file *contents*, not hardlinks, and rejects links
which might escape the project root. It is not an operating-system sandbox:
PlatformIO is still executable code and retains its normal host permissions.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile

from backend.security.workspace_io import checked_path, read_bounded

_EXCLUDE_DIRECTORIES = frozenset({".pio", ".git", ".svn", ".hg", ".venv", "venv",
                                  "node_modules", "__pycache__", ".next", ".pytest_cache"})
_MAX_FILES = 10_000
_MAX_TOTAL_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class BuildSourceSnapshot:
    root: Path
    revision: str
    files: int
    total_bytes: int


def _checked_source(root: Path, path: Path):
    try:
        return checked_path(root, path)
    except ValueError as exc:
        raise ValueError("BUILD_SOURCE_SYMLINK_UNSUPPORTED") from exc


def _source_files(root: Path):
    for directory, subdirectories, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        _checked_source(root, parent)
        subdirectories.sort()
        for name in list(subdirectories):
            folder = parent / name
            _checked_source(root, folder)
            if name.casefold() in _EXCLUDE_DIRECTORIES:
                subdirectories.remove(name)
        for name in sorted(filenames):
            source = parent / name
            _checked_source(root, source)
            if source.is_file():
                yield source, source.relative_to(root)


@contextmanager
def isolated_build_sources(project: str | Path):
    original = Path(project).expanduser().resolve(strict=True)
    if not original.is_dir() or not (original / "platformio.ini").is_file():
        raise ValueError("BUILD_SOURCE_PROJECT_INVALID")
    with tempfile.TemporaryDirectory(prefix="forgex-snapshot-") as folder:
        captured = Path(folder) / "project"
        captured.mkdir()
        hasher = hashlib.sha256()
        file_count = size = 0
        captured_sources = {}
        captured_sizes = {}
        for source, relative in _source_files(original):
            file_count += 1
            if file_count > _MAX_FILES:
                raise ValueError("BUILD_SOURCE_FILE_LIMIT")
            # Limits are checked before and while reading, not just after.
            if _checked_source(original, source).st_size + size > _MAX_TOTAL_BYTES:
                raise ValueError("BUILD_SOURCE_SIZE_LIMIT")
            target = captured / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = read_bounded(original, source, _MAX_TOTAL_BYTES - size)
            size += len(data)
            digest = hashlib.sha256(data).digest()
            with target.open("xb") as output_file:
                output_file.write(data)
            captured_sources[relative] = digest
            captured_sizes[relative] = len(data)
            hasher.update(relative.as_posix().encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(digest)
        # Make sure a multi-file workspace did not change underneath the copy.
        # The compiler only ever reads the sealed byte copies above.
        after = {}
        for source, relative in _source_files(original):
            if relative not in captured_sizes:
                raise ValueError("BUILD_SOURCE_CHANGED_DURING_CAPTURE")
            try:
                after[relative] = hashlib.sha256(read_bounded(original, source, captured_sizes[relative])).digest()
            except ValueError as exc:
                raise ValueError("BUILD_SOURCE_CHANGED_DURING_CAPTURE") from exc
        if after != captured_sources:
            raise ValueError("BUILD_SOURCE_CHANGED_DURING_CAPTURE")
        yield BuildSourceSnapshot(captured, hasher.hexdigest(), file_count, size)


def preserve_firmware(firmware_path: str | None, artifact_store: str | Path) -> tuple[str | None, str | None]:
    """Save a successful build product outside the disposable source capture."""
    if not firmware_path:
        return None, None
    source = Path(firmware_path)
    if source.is_symlink() or not source.is_file() or source.name not in {"firmware.bin", "firmware.elf"}:
        raise ValueError("BUILD_OUTPUT_UNEXPECTED")
    if source.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("BUILD_OUTPUT_TOO_LARGE")
    data = read_bounded(source.parent.resolve(strict=True), source.absolute(), 32 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    store = Path(artifact_store)
    store.mkdir(parents=True, exist_ok=True)
    store = store.resolve(strict=True)
    destination = store
    for part in ("compiled-images", digest):
        destination /= part
        destination.mkdir(exist_ok=True)
        checked_path(store, destination)
    target = destination / source.name
    if target.exists():
        if hashlib.sha256(read_bounded(store, target, 32 * 1024 * 1024)).hexdigest() != digest:
            raise ValueError("BUILD_OUTPUT_STORE_CONFLICT")
    else:
        import uuid
        temporary = destination / f".{uuid.uuid4().hex}.part"
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target), digest


def verify_build_image(firmware_path: str | Path, *, workspace_root: str | Path,
                       artifact_store: str | Path | None = None,
                       expected_sha256: str | None = None) -> tuple[Path, str]:
    """Verify a staged image or a host-owned content-addressed build image.

    An external path is authorized only by the configured host store, never by
    a path or digest supplied in model output. Reparse points are rejected before
    reading, and every refresh rechecks bytes against the original digest.
    """
    path = Path(firmware_path).expanduser().absolute()
    workspace = Path(workspace_root).resolve(strict=True)
    content_addressed = False
    if path.is_relative_to(workspace):
        root = workspace
    else:
        if artifact_store is None:
            raise ValueError("BUILD_OUTPUT_OUTSIDE_WORKSPACE")
        root = Path(artifact_store).resolve(strict=True)
        relative = path.relative_to(root)
        if (len(relative.parts) != 3 or relative.parts[0] != "compiled-images"
                or not re.fullmatch(r"[0-9a-f]{64}", relative.parts[1])
                or relative.parts[2] not in {"firmware.bin", "firmware.elf"}):
            raise ValueError("BUILD_OUTPUT_STORE_PATH_INVALID")
        content_addressed = True
    data = read_bounded(root, path, 32 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    if ((content_addressed and path.parent.name != digest)
            or (expected_sha256 is not None and digest != expected_sha256)):
        raise ValueError("BUILD_OUTPUT_HASH_MISMATCH")
    return path, digest
