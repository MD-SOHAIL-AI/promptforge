"""Persistent diagnostics for generation attempts and chunked runs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..model_router.storage import ProviderSettingsStorage


class GenerationDiagnosticsStore:
    """Append-only local JSONL store for generation telemetry."""

    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root).expanduser().resolve() if root is not None else ProviderSettingsStorage().path.parent
        self.root = base
        self.attempts_path = base / "generation-attempts.jsonl"
        self.chunked_runs_path = base / "chunked-generation-runs.jsonl"

    def record_attempt(self, record: Mapping[str, Any]) -> None:
        self._append(self.attempts_path, record)

    def record_chunked_run(self, record: Mapping[str, Any]) -> None:
        self._append(self.chunked_runs_path, record)

    def list_attempts(
        self,
        *,
        task_id: str | None = None,
        execution_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = self._read(self.attempts_path)
        if task_id is not None:
            records = [item for item in records if item.get("task_id") == task_id]
        if execution_id is not None:
            records = [item for item in records if item.get("execution_id") == execution_id]
        return records[-max(1, min(limit, 1000)) :]

    def list_chunked_runs(
        self,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        records = self._read(self.chunked_runs_path)
        if execution_id is not None:
            records = [item for item in records if item.get("execution_id") == execution_id]
        if project_id is not None:
            records = [item for item in records if item.get("project_id") == project_id]
        return records[-max(1, min(limit, 1000)) :]

    def clear(self) -> None:
        for path in (self.attempts_path, self.chunked_runs_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def _append(self, path: Path, record: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        compact = _json_safe(dict(record))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(compact, ensure_ascii=True, sort_keys=True) + "\n")

    def _read(self, path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
