"""Atomic persistence for sanitized ForgeX provider run summaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping


ALLOWED_SUMMARY_KEYS = frozenset({
    "run_id", "requested_provider", "actual_provider", "provider_type", "selected_model",
    "auth_status", "generation_source", "workspace_mode", "project_type", "changed_files",
    "unchanged_files", "created_files", "modified_files", "deleted_files", "no_op_status",
    "content_verification_status", "generation_status", "build_status", "flash_status",
    "monitor_status", "fallback_used", "fallback_reason", "errors", "warnings",
    "next_suggested_action",
})


class ProviderRunSummaryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def save(self, summary: Mapping[str, object]) -> None:
        run_id = summary.get("run_id")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
            raise ValueError("run_summary_id_invalid")
        safe = {key: value for key, value in summary.items() if key in ALLOWED_SUMMARY_KEYS}
        records = self._read()
        records[run_id] = safe
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)

    def get(self, run_id: str) -> dict[str, object] | None:
        value = self._read().get(run_id)
        return dict(value) if isinstance(value, dict) else None

    def _read(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}
