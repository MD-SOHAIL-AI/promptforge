"""Safe backend adapter for the standalone Codex OAuth smoke QA command."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_login import CodexLoginService, PROVIDER_ID

SAFE_FIELDS = {
    "classification", "provider_id", "auth_status", "oauth_bridge_ready", "run_id",
    "sandbox_kind", "argv_shape", "shell_false", "dangerous_flags_used", "execution_count",
    "created_file_count", "modified_file_count", "deleted_file_count", "expected_file_created",
    "expected_content_valid", "marker_unchanged", "active_workspace_unchanged", "review_created",
    "review_id", "production_routing_enabled", "auto_apply", "auto_build", "auto_flash",
    "tokens_read", "auth_files_read", "content_validation_mode", "normalization_applied",
    "normalized_content_matches", "first_difference_kind", "prompt_variant",
    "raw_prompt_persisted", "raw_output_persisted",
}


@dataclass(frozen=True, slots=True)
class CodexOAuthSmokeResult:
    payload: dict[str, object]

    def to_safe_dict(self) -> dict[str, object]:
        return {key: value for key, value in self.payload.items() if key in SAFE_FIELDS}


class CodexOAuthSmokeService:
    def __init__(self, *, login_service: CodexLoginService, repository_root: str | Path,
                 command_runner: Callable[..., Any] | None = None, feature_enabled: bool | None = None) -> None:
        self.login_service = login_service
        self.repository_root = Path(repository_root).resolve()
        self.command_runner = command_runner or subprocess.run
        self.feature_enabled = os.getenv("FORGEX_ENABLE_CODEX_OAUTH_SMOKE", "").strip() == "1" if feature_enabled is None else bool(feature_enabled)

    def run(self, *, confirm_real_codex: bool) -> CodexOAuthSmokeResult:
        if confirm_real_codex is not True:
            return self._result("CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED")
        status = self.login_service.status()
        if not status.oauth_bridge_ready:
            classification = "CODEX_OAUTH_SMOKE_LOGIN_REQUIRED" if status.auth_status == "signed_out" else "CODEX_OAUTH_SMOKE_STATUS_UNKNOWN"
            return self._result(classification, auth_status=status.auth_status)
        if not self.feature_enabled:
            return self._result("CODEX_OAUTH_SMOKE_UNSAFE_ABORTED", auth_status=status.auth_status)
        script = self.repository_root / "scripts" / "qa-codex-oauth-bridge.mjs"
        try:
            completed = self.command_runner(
                ["node", str(script), "--standalone-smoke", "--confirm-real-codex"],
                cwd=str(self.repository_root), capture_output=True, text=True, timeout=360,
                shell=False, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return self._result("CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE", auth_status=status.auth_status)
        payload = self._parse_safe_output(str(getattr(completed, "stdout", "")))
        classification = payload.get("classification")
        if not isinstance(classification, str) or not classification.startswith("CODEX_OAUTH_SMOKE_"):
            return self._result("CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE", auth_status=status.auth_status)
        payload.setdefault("provider_id", PROVIDER_ID)
        if classification == "CODEX_OAUTH_SMOKE_PASS" and not self._is_safe_pass(payload):
            payload["classification"] = "CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE"
            payload["review_created"] = False
            payload["review_id"] = None
        return CodexOAuthSmokeResult(payload)

    def _result(self, classification: str, *, auth_status: str = "unknown") -> CodexOAuthSmokeResult:
        return CodexOAuthSmokeResult({
            "classification": classification, "provider_id": PROVIDER_ID, "auth_status": auth_status,
            "oauth_bridge_ready": False, "review_created": False, "review_id": None,
            "created_file_count": 0, "modified_file_count": 0, "deleted_file_count": 0,
            "active_workspace_unchanged": True, "production_routing_enabled": False,
            "tokens_read": False, "auth_files_read": False,
        })

    @staticmethod
    def _parse_safe_output(output: str) -> dict[str, object]:
        payload: dict[str, object] = {}
        for line in output.splitlines():
            key, separator, raw = line.partition(" = ")
            if not separator or key not in SAFE_FIELDS:
                continue
            value: object = raw
            if raw in {"true", "false"}: value = raw == "true"
            elif key.endswith("_count") or key == "execution_count":
                try: value = int(raw)
                except ValueError: continue
            elif raw in {"null", "None"}: value = None
            payload[key] = value
        return payload

    @staticmethod
    def _is_safe_pass(payload: dict[str, object]) -> bool:
        required_true = (
            "oauth_bridge_ready", "shell_false", "expected_file_created", "expected_content_valid",
            "normalized_content_matches", "marker_unchanged", "active_workspace_unchanged",
        )
        required_false = (
            "dangerous_flags_used", "tokens_read", "auth_files_read", "raw_prompt_persisted",
            "raw_output_persisted", "production_routing_enabled", "auto_apply", "auto_build", "auto_flash",
        )
        return (
            payload.get("classification") == "CODEX_OAUTH_SMOKE_PASS"
            and payload.get("auth_status") == "signed_in"
            and payload.get("execution_count") == 1
            and payload.get("sandbox_kind") == "external_disposable_oauth_smoke"
            and payload.get("argv_shape") == "global_approval_before_exec"
            and payload.get("created_file_count") == 1
            and payload.get("modified_file_count") == 0
            and payload.get("deleted_file_count") == 0
            and all(payload.get(name) is True for name in required_true)
            and all(payload.get(name) is False for name in required_false)
        )
