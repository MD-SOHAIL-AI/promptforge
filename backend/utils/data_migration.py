"""Non-destructive, restartable migration to a per-user desktop data directory."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat


def migrate_legacy_data(source: str | Path, target: str | Path) -> bool:
    source, target = Path(source).absolute(), Path(target).absolute()
    if source == target or not source.is_dir():
        return False
    target.mkdir(parents=True, exist_ok=True)
    for root in (source, target):
        for entry in (*reversed(root.parents), root):
            _reject_link(entry)
    marker = target / "migration-v1.json"
    if marker.exists():
        record = json.loads(marker.read_text(encoding="utf-8"))
        if record.get("status") == "complete":
            return True
        if record.get("source") != str(source):
            raise ValueError("DATA_MIGRATION_SOURCE_CHANGED")
    else:
        if any((target / name).exists() for name in (".promptforge", "workspace")):
            raise ValueError("DATA_MIGRATION_TARGET_NOT_EMPTY")
        record = {"version": 1, "source": str(source), "status": "prepared", "directories": {}}
        _write_marker(marker, record)
    for name in (".promptforge", "workspace"):
        old, destination = source / name, target / name
        if not old.exists():
            continue
        manifest = record["directories"].get(name)
        if manifest is not None:
            if not destination.exists():
                staged = target / ("migration-stage-" + name.lstrip("."))
                _verify_manifest(staged, manifest)
                staged.rename(destination)
            _verify_manifest(destination, manifest)
            continue
        staged = target / ("migration-stage-" + name.lstrip("."))
        staged.mkdir(exist_ok=True)
        manifest = {}
        for directory, directories, files in os.walk(old, followlinks=False):
            current = Path(directory)
            for entry in (current, *(current / child for child in directories), *(current / file for file in files)):
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("DATA_MIGRATION_LINK_UNSAFE")
            for file in files:
                original = current / file
                if file.endswith(("-wal", "-shm")):
                    continue  # SQLite backup captures committed WAL content.
                relative = original.relative_to(old).as_posix()
                copied = staged / relative
                copied.parent.mkdir(parents=True, exist_ok=True)
                if original.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
                    with closing(sqlite3.connect(original)) as reader, closing(sqlite3.connect(copied)) as writer:
                        reader.backup(writer)
                        if writer.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                            raise ValueError("DATA_MIGRATION_DATABASE_CORRUPT")
                else:
                    shutil.copyfile(original, copied)
                    if _digest(original) != _digest(copied):
                        raise ValueError("DATA_MIGRATION_SOURCE_CHANGED")
                with copied.open("rb+") as handle:
                    os.fsync(handle.fileno())
                manifest[relative] = _digest(copied)
        record["directories"][name] = manifest
        _write_marker(marker, record)
        _verify_manifest(staged, manifest)
        staged.rename(destination)
    record["status"] = "complete"
    _write_marker(marker, record)
    return True


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _verify_manifest(root: Path, manifest: dict[str, str]) -> None:
    for relative, expected in manifest.items():
        candidate = root / relative
        for entry in (candidate, *candidate.parents):
            _reject_link(entry)
            if entry == root:
                break
        if not candidate.is_file() or _digest(candidate) != expected:
            raise ValueError("DATA_MIGRATION_COPY_INVALID")


def _reject_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("DATA_MIGRATION_LINK_UNSAFE")


def _write_marker(path: Path, record: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
