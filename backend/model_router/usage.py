"""Lightweight model-router usage tracking."""

from __future__ import annotations

import json
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
