"""Bounded reads within a selected workspace, including Python 3.10 on Windows."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from .secret_scanner import has_high_confidence_secret


def checked_path(root: Path, path: Path):
    # The selected root may itself be a user-selected junction. Callers
    # canonicalize it once; every descendant must remain a regular component.
    root = root.resolve(strict=True)
    relative = path.absolute().relative_to(root)
    if ".." in relative.parts:
        raise ValueError("WORKSPACE_PATH_UNSAFE")
    current = root
    info = current.lstat()
    for part in relative.parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("WORKSPACE_LINK_UNSAFE")
    current.resolve(strict=True).relative_to(root)
    return info


def read_bounded(root: Path, path: Path, limit: int) -> bytes:
    before = checked_path(root, path)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("WORKSPACE_FILE_UNSAFE")
    if before.st_size > limit:
        raise ValueError("WORKSPACE_FILE_TOO_LARGE")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as reader:
        opened = os.fstat(reader.fileno())
        if _identity(before) != _identity(opened):
            raise ValueError("WORKSPACE_FILE_CHANGED")
        data = reader.read(limit + 1)
        if len(data) > limit or len(data) != before.st_size:
            raise ValueError("WORKSPACE_FILE_CHANGED")
        if _identity(opened) != _identity(os.fstat(reader.fileno())):
            raise ValueError("WORKSPACE_FILE_CHANGED")
    if _identity(before) != _identity(checked_path(root, path)):
        raise ValueError("WORKSPACE_FILE_CHANGED")
    return data


def read_context_text(root: Path, path: Path, limit: int) -> str:
    text = read_bounded(root, path, limit).decode("utf-8")
    if has_high_confidence_secret(text):
        raise ValueError("WORKSPACE_SECRET_CONTENT_FORBIDDEN")
    return text


def iter_workspace_files(root: Path, *, ignored_dirs=(), max_entries: int = 10_000):
    """Never descend into a symlink/reparse point; stop excessive traversal."""
    root = root.resolve(strict=True)
    count = 0
    ignored = {name.casefold() for name in ignored_dirs}
    for directory, directories, names in os.walk(root, followlinks=False):
        here = Path(directory)
        checked_path(root, here)
        safe = []
        for name in sorted(directories):
            count += 1
            if count > max_entries:
                raise ValueError("WORKSPACE_ENTRY_LIMIT")
            if name.casefold() in ignored:
                continue
            try:
                checked_path(root, here / name)
            except (OSError, ValueError):
                continue
            safe.append(name)
        directories[:] = safe
        for name in sorted(names):
            count += 1
            if count > max_entries:
                raise ValueError("WORKSPACE_ENTRY_LIMIT")
            path = here / name
            try:
                info = checked_path(root, path)
            except (OSError, ValueError):
                continue
            if stat.S_ISREG(info.st_mode):
                yield path


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
