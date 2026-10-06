"""Conservative cleanup of unreferenced, expired managed stages only."""
import os
from pathlib import Path
import shutil
import stat
import time


def prune_orphan_stages(root: Path, protected=(), *, max_age_days=14, limit=20):
    root = root.absolute()
    if _linked(root) or root.resolve(strict=True) != root:
        return []
    protected = {Path(path).resolve(strict=False) for path in protected}
    removed = []
    for candidate in sorted(root.iterdir()):
        if len(removed) >= limit:
            break
        if not candidate.name.startswith(("agent-v3-", "workflow-", "checkpoint-")):
            continue
        resolved = candidate.resolve(strict=False)
        if resolved.parent != root or _linked(candidate) or not candidate.is_dir():
            continue
        if any(path == resolved or path.is_relative_to(resolved) for path in protected):
            continue
        if time.time() - candidate.stat().st_mtime < max_age_days * 86400:
            continue
        safe, count = True, 0
        for directory, names, files in os.walk(candidate, followlinks=False):
            count += len(names) + len(files)
            if count > 10000 or any(_linked(Path(directory) / name) for name in (*names, *files)):
                safe = False
                break
        if safe and candidate.resolve(strict=True).parent == root:
            shutil.rmtree(candidate)
            removed.append(candidate.name)
    return removed


def _linked(path):
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def referenced_stages(root: Path, payloads):
    protected = set()
    def visit(value):
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            try:
                path = Path(value)
                if path.is_absolute() and path.is_relative_to(root):
                    protected.add(path)
            except (ValueError, OSError):
                pass
    for payload in payloads:
        visit(payload)
    return protected
