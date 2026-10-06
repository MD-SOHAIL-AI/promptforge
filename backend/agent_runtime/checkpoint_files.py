"""Copy bounded recovery data without following symlinks or Windows junctions."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from backend.changes.service import IGNORED_DIRS, is_safe_relative_path


def _checked(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("AGENT_CHECKPOINT_LINK_UNSAFE")
    return info


def copy_checkpoint_tree(source: str | Path, target: str | Path) -> None:
    source, target = Path(source).absolute(), Path(target).absolute()
    # Check the lexical path before resolve can hide a junction.
    for root in (source, target):
        for part in (*reversed(root.parents), root):
            _checked(part)
    count, total = 0, 0
    for directory, directories, files in os.walk(source, followlinks=False):
        here = Path(directory)
        _checked(here)
        for name in directories:
            _checked(here / name)
        directories[:] = [name for name in directories if name not in IGNORED_DIRS]
        for name in files:
            original = here / name
            info = _checked(original)
            relative = original.relative_to(source).as_posix()
            if not is_safe_relative_path(relative):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("AGENT_CHECKPOINT_FILE_UNSAFE")
            count += 1
            total += info.st_size
            if count > 10_000 or total > 256 * 1024 * 1024:
                raise ValueError("AGENT_CHECKPOINT_SIZE_LIMIT")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            for parent in (destination.parent, *destination.parent.parents):
                _checked(parent)
                if parent == target:
                    break
            if destination.exists() or destination.is_symlink():
                _checked(destination)
            descriptor = os.open(original, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as reader:
                opened = os.fstat(reader.fileno())
                if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                    raise ValueError("AGENT_CHECKPOINT_SOURCE_CHANGED")
                with destination.open("wb") as writer:
                    remaining = info.st_size
                    while remaining:
                        chunk = reader.read(min(remaining, 1024 * 1024))
                        if not chunk:
                            raise ValueError("AGENT_CHECKPOINT_SOURCE_CHANGED")
                        writer.write(chunk)
                        remaining -= len(chunk)
                    if reader.read(1):
                        raise ValueError("AGENT_CHECKPOINT_SOURCE_CHANGED")
                    writer.flush()
                    os.fsync(writer.fileno())
                after = _checked(original)
                if (info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
                    raise ValueError("AGENT_CHECKPOINT_SOURCE_CHANGED")
