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
    RUNTIME_FAILED = "runtime_failed"


class ProviderErrorCode(str, Enum):
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_API_KEY = "INVALID_API_KEY"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_BUSY = "PROVIDER_BUSY"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_HTTP_ERROR = "PROVIDER_HTTP_ERROR"
    PROVIDER_AUTH_ERROR = "PROVIDER_AUTH_ERROR"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_ENVELOPE_INVALID = "PROVIDER_ENVELOPE_INVALID"
    PROVIDER_CONTENT_MISSING = "PROVIDER_CONTENT_MISSING"
    PROVIDER_STREAM_INVALID = "PROVIDER_STREAM_INVALID"
    MODEL_CAPABILITY_UNSUPPORTED = "MODEL_CAPABILITY_UNSUPPORTED"
    MODEL_OUTPUT_EMPTY = "MODEL_OUTPUT_EMPTY"
    MODEL_OUTPUT_TRUNCATED = "MODEL_OUTPUT_TRUNCATED"
    MODEL_OUTPUT_NOT_JSON = "MODEL_OUTPUT_NOT_JSON"
    TOOLPLAN_SCHEMA_INVALID = "TOOLPLAN_SCHEMA_INVALID"
    TOOLPLAN_VERSION_INVALID = "TOOLPLAN_VERSION_INVALID"
    TOOLPLAN_TOOL_UNKNOWN = "TOOLPLAN_TOOL_UNKNOWN"
    TOOL_ARGUMENTS_INVALID = "TOOL_ARGUMENTS_INVALID"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    PATH_UNSAFE = "PATH_UNSAFE"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    POLICY_DENIED = "POLICY_DENIED"
    FORGEX_NORMALIZATION_ERROR = "FORGEX_NORMALIZATION_ERROR"
    FORGEX_RUNTIME_ERROR = "FORGEX_RUNTIME_ERROR"
    FORGEX_INTERNAL_ERROR = "FORGEX_INTERNAL_ERROR"
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
    exact = {
        code.value.casefold(): code
        for code in ProviderErrorCode
    }
    if text in exact:
        return exact[text]
    if "key_missing" in text or "missing_api_key" in text:
        return ProviderErrorCode.MISSING_API_KEY
    if "auth_invalid" in text or "invalid_api_key" in text or "auth_error" in text:
        return ProviderErrorCode.PROVIDER_AUTH_ERROR
    if "timeout" in text:
        return ProviderErrorCode.PROVIDER_TIMEOUT
    if "rate" in text and "limit" in text or "quota" in text:
        return ProviderErrorCode.PROVIDER_RATE_LIMITED
    if "stream" in text and "invalid" in text:
        return ProviderErrorCode.PROVIDER_STREAM_INVALID
    if "envelope" in text or "response_shape" in text:
        return ProviderErrorCode.PROVIDER_ENVELOPE_INVALID
    if "content_missing" in text:
        return ProviderErrorCode.PROVIDER_CONTENT_MISSING
    if "output_empty" in text:
        return ProviderErrorCode.MODEL_OUTPUT_EMPTY
    if "output_truncated" in text:
        return ProviderErrorCode.MODEL_OUTPUT_TRUNCATED
    if "not_json" in text or "invalid_json" in text:
        return ProviderErrorCode.MODEL_OUTPUT_NOT_JSON
    if "toolplan" in text and "version" in text:
        return ProviderErrorCode.TOOLPLAN_VERSION_INVALID
    if "toolplan" in text and "schema" in text:
        return ProviderErrorCode.TOOLPLAN_SCHEMA_INVALID
    if "smoke" in text or "toolplan_unsafe" in text:
        return ProviderErrorCode.POLICY_DENIED
    if "toolplan" in text or "arguments_invalid" in text:
        return ProviderErrorCode.TOOL_ARGUMENTS_INVALID
    if "tool" in text and ("forbidden" in text or "not_allowed" in text):
        return ProviderErrorCode.TOOL_NOT_ALLOWED
    if "path_unsafe" in text:
        return ProviderErrorCode.PATH_UNSAFE
    if "file_not_found" in text or "directory_missing" in text:
        return ProviderErrorCode.FILE_NOT_FOUND
    if "policy_denied" in text:
        return ProviderErrorCode.POLICY_DENIED
    if "toolplan_no_progress" in text:
        return ProviderErrorCode.FORGEX_RUNTIME_ERROR
    if text.startswith(("agent_runtime_", "tool_runtime_", "build_", "simulation_")):
        return ProviderErrorCode.FORGEX_RUNTIME_ERROR
    if "response" in text:
        return ProviderErrorCode.PROVIDER_INVALID_RESPONSE
    if "content" in text or "path_unsafe" in text or "output" in text or "changeset" in text:
        return ProviderErrorCode.INVALID_GENERATED_OUTPUT
    if text in {"api_network_error", "api_model_unavailable", "provider_unavailable"}:
        return ProviderErrorCode.PROVIDER_UNAVAILABLE
    return ProviderErrorCode.FORGEX_RUNTIME_ERROR


def model_recovery_message(initial: str | None, terminal: str | None, recovery: str | None = None, skip: str | None = None) -> str | None:
    causes = {
        "MODEL_OUTPUT_TRUNCATED": "The model response was truncated before it produced a complete answer or action.",
        "MODEL_OUTPUT_EMPTY": "The model returned no usable answer or action.",
        "PROVIDER_STREAM_INVALID": "The provider stream ended before a complete response arrived.",
        "TOOL_ARGUMENTS_INVALID": "The model returned incomplete or invalid tool arguments.",
    }
    cause = causes.get(initial or "")
    if not cause:
        return None
    if terminal == "AGENT_TURN_LIMIT_EXCEEDED":
        return cause + " ForgeX's recovery reached the local time limit. The saved draft can be continued and validated."
    if skip:
        return cause + " ForgeX preserved the draft because the remaining time or provider budget could not support a repair."
    if recovery:
        return cause + " The repair response also failed. The saved draft can be continued and validated."
    return None


def runtime_failure_message(classification: str | None) -> str:
    if classification == "FORGEX_REQUEST_INVALID":
        return "ForgeX could not serialize the request locally. No provider request was sent for this attempt. Your saved work is preserved."
    if classification == "AGENT_RUNTIME_NO_PROGRESS":
        return "ForgeX stopped after repeated tool turns produced no new information or file changes."
    if classification == "AGENT_RUNTIME_MAX_TURNS":
        return "ForgeX reached its reasoning-turn limit before completing the requested files."
    if classification == "AGENT_RUNTIME_REPEATED_CALL":
        return "ForgeX stopped repeated tool calls that were making no progress."
    if classification == "REQUIREMENTS_UNVERIFIED":
        return "Generated files are saved, but the requested requirements are not all verified. Review the draft or retry."
    if classification == "AGENT_RUNTIME_INTERRUPTED":
        return "The backend stopped during this run. Review the saved work and confirm a safe continuation."
    return "ForgeX could not continue the Agent runtime."


def actionable_message(code: ProviderErrorCode, provider_name: str) -> str:
    messages = {
        ProviderErrorCode.MISSING_API_KEY: f"{provider_name} has no API key configured. Add one in Model Settings or choose another provider.",
        ProviderErrorCode.INVALID_API_KEY: f"{provider_name} rejected the configured API key. Replace it in Model Settings.",
        ProviderErrorCode.PROVIDER_RATE_LIMITED: f"{provider_name} is rate limited. Retry later or use the configured fallback.",
        ProviderErrorCode.PROVIDER_UNAVAILABLE: f"{provider_name}'s selected model endpoint is temporarily unavailable. Retry the same model; ForgeX will not silently send project data to another provider.",
        ProviderErrorCode.PROVIDER_BUSY: "Another ForgeX task is using this model endpoint. This task paused locally before sending a provider request. Stop the other task or retry when it finishes.",
        ProviderErrorCode.PROVIDER_INVALID_RESPONSE: f"{provider_name} returned an invalid response.",
        ProviderErrorCode.PROVIDER_HTTP_ERROR: f"{provider_name} could not complete the request.",
        ProviderErrorCode.PROVIDER_AUTH_ERROR: f"{provider_name} rejected the configured credentials. Update them in Model Settings.",
        ProviderErrorCode.PROVIDER_TIMEOUT: f"{provider_name} timed out before completing the response.",
        ProviderErrorCode.PROVIDER_ENVELOPE_INVALID: f"{provider_name} returned a response envelope ForgeX does not recognize.",
        ProviderErrorCode.PROVIDER_CONTENT_MISSING: "The model request succeeded, but it returned no final answer or tool call.",
        ProviderErrorCode.PROVIDER_STREAM_INVALID: f"{provider_name} returned an invalid streaming packet.",
        ProviderErrorCode.MODEL_CAPABILITY_UNSUPPORTED: "The selected model endpoint does not support the tool calling required by Agent mode. Choose a tool-capable model or refresh the model catalog and retry.",
        ProviderErrorCode.MODEL_OUTPUT_EMPTY: "The model completed without producing an answer or tool plan.",
        ProviderErrorCode.MODEL_OUTPUT_TRUNCATED: "The model stopped before its answer or tool plan was complete.",
        ProviderErrorCode.MODEL_OUTPUT_NOT_JSON: "The model responded successfully, but ForgeX could not convert the output into JSON for a tool plan.",
        ProviderErrorCode.TOOLPLAN_SCHEMA_INVALID: "The model responded successfully, but ForgeX could not convert the output into a valid tool plan.",
        ProviderErrorCode.TOOLPLAN_VERSION_INVALID: "The model returned a tool plan version this ForgeX runtime does not support.",
        ProviderErrorCode.TOOLPLAN_TOOL_UNKNOWN: "The model requested a tool that ForgeX does not recognize.",
        ProviderErrorCode.TOOL_ARGUMENTS_INVALID: "The model returned invalid arguments for a ForgeX tool.",
        ProviderErrorCode.TOOL_NOT_ALLOWED: "The requested tool is not allowed for this Agent mode.",
        ProviderErrorCode.PATH_UNSAFE: "The model requested a path outside the active project.",
        ProviderErrorCode.FILE_NOT_FOUND: "The requested file or directory does not exist in the active project.",
        ProviderErrorCode.POLICY_DENIED: "ForgeX policy denied the requested operation.",
        ProviderErrorCode.FORGEX_NORMALIZATION_ERROR: "ForgeX could not normalize the model response.",
        ProviderErrorCode.FORGEX_RUNTIME_ERROR: "ForgeX could not continue the Agent runtime.",
        ProviderErrorCode.FORGEX_INTERNAL_ERROR: "ForgeX encountered an internal model-processing error.",
        ProviderErrorCode.INVALID_GENERATED_OUTPUT: f"{provider_name} returned output that ForgeX rejected during ChangeSet validation.",
    }
    return messages.get(code, f"{provider_name} is currently unavailable.")
