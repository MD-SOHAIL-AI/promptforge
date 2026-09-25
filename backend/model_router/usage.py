"""Lightweight model-router usage tracking."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import UsageRecord
from .storage import ProviderSettingsStorage


class UsageTracker:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is not None:
            self.path = Path(path)
        else:
            self.path = ProviderSettingsStorage().path.with_name("usage.jsonl")

    def record(self, record: UsageRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=True, sort_keys=True) + "\n")

    def list_records(self, limit: int = 100) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, object]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
        return rows[-limit:]


class SQLiteUsageTracker:
    """Append usage transactionally without rereading an unbounded JSONL log."""
    def __init__(self, path: Path, *, legacy_jsonl_path: Path | None = None):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=30)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS model_usage(id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS usage_imports(source TEXT PRIMARY KEY)")
            # Import and marker share one transaction: concurrent startup or a
            # crash cannot duplicate the history. The original remains intact.
            connection.execute("BEGIN IMMEDIATE")
            if legacy_jsonl_path is not None and legacy_jsonl_path.is_file():
                source = str(legacy_jsonl_path.resolve())
                imported = connection.execute("SELECT 1 FROM usage_imports WHERE source=?", (source,)).fetchone()
                if not imported:
                    with legacy_jsonl_path.open(encoding="utf-8") as handle:
                        for line in handle:
                            try:
                                value = json.loads(line)
                            except json.JSONDecodeError:
                                continue  # Match the historical JSONL reader.
                            if isinstance(value, dict):
                                connection.execute("INSERT INTO model_usage(payload) VALUES(?)", (json.dumps(value),))
                    connection.execute("INSERT INTO usage_imports VALUES(?)", (source,))

    def record(self, record: UsageRecord) -> None:
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.execute("INSERT INTO model_usage(payload) VALUES(?)", (json.dumps(record.to_dict()),))

    def list_records(self, limit: int = 100) -> list[dict[str, object]]:
        limit = max(0, min(int(limit), 10000))
        with closing(sqlite3.connect(self.path, timeout=30)) as connection:
            rows = connection.execute("SELECT payload FROM model_usage ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]
