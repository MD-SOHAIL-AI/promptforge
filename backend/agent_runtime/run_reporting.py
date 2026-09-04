"""Sanitized run summaries for the product agent runtime."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class ProviderType(str, Enum):
    API = "api_provider"
    TEMPLATE = "template_provider"


class WorkspaceMode(str, Enum):
    MODIFY_EXISTING = "modify_existing_project"
    GENERATE_INTO_FOLDER = "generate_into_open_folder"


class GenerationStatus(str, Enum):
    STARTED = "generation_started"
    CONTENT_VERIFIED = "content_verified"
    PROVIDER_FAILED = "provider_failed"


class ProviderErrorCode(str, Enum):
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_API_KEY = "INVALID_API_KEY"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    INVALID_GENERATED_OUTPUT = "INVALID_GENERATED_OUTPUT"


@dataclass(slots=True)
class ForgeXRunSummary:
    run_id: str
    requested_provider: str
    actual_provider: str | None = None
    provider_type: ProviderType | None = None
    selected_model: str | None = None
    auth_status: str = "unknown"
    generation_source: str | None = None
    workspace_mode: WorkspaceMode = WorkspaceMode.GENERATE_INTO_FOLDER
    project_type: str = "generic"
    changed_files: list[str] = field(default_factory=list)
    unchanged_files: list[str] = field(default_factory=list)
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    no_op_status: bool = False
    content_verification_status: str = "pending"
    generation_status: str = GenerationStatus.STARTED.value
    build_status: str = "not_started"
    flash_status: str = "not_started"
    monitor_status: str = "not_started"
    fallback_used: bool = False
    fallback_reason: str | None = None
    errors: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_suggested_action: str | None = None

    def to_safe_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["provider_type"] = self.provider_type.value if self.provider_type else None
        value["workspace_mode"] = self.workspace_mode.value
        return value


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
        self._lock = threading.RLock()

    def save(self, summary: Mapping[str, object]) -> None:
        run_id = summary.get("run_id")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
            raise ValueError("run_summary_id_invalid")
        safe = {key: value for key, value in summary.items() if key in ALLOWED_SUMMARY_KEYS}
        with self._lock:
            records = self._read_unlocked()
            records[run_id] = safe
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(records, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def get(self, run_id: str) -> dict[str, object] | None:
        with self._lock:
            value = self._read_unlocked().get(run_id)
            return dict(value) if isinstance(value, dict) else None

    def _read_unlocked(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}


def detect_workspace_mode(workspace_root: str | Path) -> WorkspaceMode:
    root = Path(workspace_root).resolve(strict=True)
    return WorkspaceMode.MODIFY_EXISTING if (root / "platformio.ini").is_file() else WorkspaceMode.GENERATE_INTO_FOLDER


def normalize_provider_error(value: str | None) -> ProviderErrorCode:
    text = (value or "").casefold()
    if "key_missing" in text or "missing_api_key" in text:
        return ProviderErrorCode.MISSING_API_KEY
    if "auth_invalid" in text or "invalid_api_key" in text:
        return ProviderErrorCode.INVALID_API_KEY
    if "rate" in text and "limit" in text or "quota" in text:
        return ProviderErrorCode.PROVIDER_RATE_LIMITED
    if "response" in text or "schema" in text or "json" in text or "toolplan_invalid" in text:
        return ProviderErrorCode.PROVIDER_INVALID_RESPONSE
    if "content" in text or "path_unsafe" in text or "output" in text or "changeset" in text or "toolplan_unsafe" in text:
        return ProviderErrorCode.INVALID_GENERATED_OUTPUT
    return ProviderErrorCode.PROVIDER_UNAVAILABLE


def actionable_message(code: ProviderErrorCode, provider_name: str) -> str:
    messages = {
        ProviderErrorCode.MISSING_API_KEY: f"{provider_name} has no API key configured. Add one in Model Settings or choose another provider.",
        ProviderErrorCode.INVALID_API_KEY: f"{provider_name} rejected the configured API key. Replace it in Model Settings.",
        ProviderErrorCode.PROVIDER_RATE_LIMITED: f"{provider_name} is rate limited. Retry later or use the configured fallback.",
        ProviderErrorCode.PROVIDER_INVALID_RESPONSE: f"{provider_name} returned an invalid response.",
        ProviderErrorCode.INVALID_GENERATED_OUTPUT: f"{provider_name} returned output that ForgeX rejected during ChangeSet validation.",
    }
    return messages.get(code, f"{provider_name} is currently unavailable.")
