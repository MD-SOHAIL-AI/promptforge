from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from backend.bridges.codex_login import CodexLoginService
from backend.bridges.codex_oauth_smoke import CodexOAuthSmokeService
from backend.bridges.codex_oauth_smoke_review import (
    MARKER_CONTENT, MARKER_NAME, SMOKE_CONTENT, SMOKE_NAME,
    create_codex_oauth_smoke_review,
)
from backend.bridges.diff_service import BridgeDiffService


class Detector:
    def __init__(self, auth_status: str) -> None: self.auth_status = auth_status
    def detect(self):
        return SimpleNamespace(installed=True, version="codex-cli 0.142.5", auth_status=self.auth_status,
                               checked_commands=("codex --version", "codex login --help", "codex login status"))


def service(tmp_path: Path, auth: str = "authenticated", output: str = "") -> tuple[CodexOAuthSmokeService, list[object]]:
    calls: list[object] = []
    def runner(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, output, "raw stderr is ignored")
    login = CodexLoginService(detector=Detector(auth))  # type: ignore[arg-type]
    return CodexOAuthSmokeService(login_service=login, repository_root=tmp_path, command_runner=runner, feature_enabled=True), calls


def test_smoke_refuses_without_confirmation(tmp_path: Path) -> None:
    subject, calls = service(tmp_path)
    assert subject.run(confirm_real_codex=False).to_safe_dict()["classification"] == "CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED"
    assert calls == []


def test_smoke_refuses_signed_out_and_unknown(tmp_path: Path) -> None:
    signed_out, calls = service(tmp_path, "unauthenticated")
    assert signed_out.run(confirm_real_codex=True).to_safe_dict()["classification"] == "CODEX_OAUTH_SMOKE_LOGIN_REQUIRED"
    unknown, unknown_calls = service(tmp_path, "unknown")
    assert unknown.run(confirm_real_codex=True).to_safe_dict()["classification"] == "CODEX_OAUTH_SMOKE_STATUS_UNKNOWN"
    assert calls == unknown_calls == []


def test_backend_adapter_uses_direct_node_argv_and_returns_only_safe_fields(tmp_path: Path) -> None:
    output = "\n".join([
        "classification = CODEX_OAUTH_SMOKE_PASS", "provider_id = codex_cli_oauth_bridge",
        "auth_status = signed_in", "oauth_bridge_ready = true", "execution_count = 1",
        "sandbox_kind = external_disposable_oauth_smoke", "argv_shape = global_approval_before_exec",
        "shell_false = true", "dangerous_flags_used = false", "review_created = true",
        "review_id = bridge-review-safe", "created_file_count = 1", "modified_file_count = 0",
        "deleted_file_count = 0", "expected_file_created = true", "expected_content_valid = true",
        "normalized_content_matches = true", "marker_unchanged = true", "active_workspace_unchanged = true",
        "production_routing_enabled = false", "tokens_read = false", "auth_files_read = false",
        "raw_prompt_persisted = false", "raw_output_persisted = false",
        "auto_apply = false", "auto_build = false", "auto_flash = false",
        "stdout = forbidden", "access_token = forbidden",
    ])
    subject, calls = service(tmp_path, output=output)
    payload = subject.run(confirm_real_codex=True).to_safe_dict()
    assert payload["classification"] == "CODEX_OAUTH_SMOKE_PASS" and payload["review_id"] == "bridge-review-safe"
    args, kwargs = calls[0]
    assert args[-2:] == ["--standalone-smoke", "--confirm-real-codex"]
    assert kwargs["shell"] is False
    assert "stdout" not in payload and "access_token" not in payload


def test_exact_review_contains_only_expected_created_file_and_safe_metadata(tmp_path: Path) -> None:
    sandbox = tmp_path / "oauth-0123456789abcdef01234567"; sandbox.mkdir()
    (sandbox / ".git").mkdir()
    (sandbox / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    (sandbox / SMOKE_NAME).write_text(SMOKE_CONTENT, encoding="utf-8")
    reviews = BridgeDiffService()
    review_id = create_codex_oauth_smoke_review(sandbox, reviews)
    review = reviews.get_review(review_id)
    assert review.provider_id == "codex_cli_oauth_bridge"
    assert [(item.path, item.change_type) for item in review.changed_files] == [(SMOKE_NAME, "created")]
    assert review.artifact_metadata and review.artifact_metadata["classification"] == "CODEX_OAUTH_SMOKE_PASS"
    assert review.artifact_metadata["content_validation_mode"] == "normalized_single_line"
    assert review.artifact_metadata["prompt_variant"] == "strict_single_line_v2"
    assert review.artifact_metadata["production_routing_enabled"] is False


def test_review_accepts_only_safe_eof_newline_normalization(tmp_path: Path) -> None:
    sandbox = tmp_path / "oauth-fedcba9876543210fedcba98"; sandbox.mkdir(); (sandbox / ".git").mkdir()
    (sandbox / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    (sandbox / SMOKE_NAME).write_bytes((SMOKE_CONTENT + "\r\n").encode("utf-8"))
    review_id = create_codex_oauth_smoke_review(sandbox, BridgeDiffService())
    assert review_id


def test_codex_oauth_smoke_trailing_newline_normalized_pass_creates_review(tmp_path: Path) -> None:
    sandbox = tmp_path / "oauth-aabbccddeeff001122334455"; sandbox.mkdir(); (sandbox / ".git").mkdir()
    (sandbox / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    (sandbox / SMOKE_NAME).write_bytes((SMOKE_CONTENT + "\n").encode("utf-8"))
    reviews = BridgeDiffService()
    review_id = create_codex_oauth_smoke_review(sandbox, reviews)
    review = reviews.get_review(review_id)
    assert review.artifact_metadata
    assert review.artifact_metadata["classification"] == "CODEX_OAUTH_SMOKE_PASS"
    assert review.artifact_metadata["normalization_applied"] == "eof_newline_or_bom_or_crlf_only"
    assert [(item.path, item.change_type) for item in review.changed_files] == [(SMOKE_NAME, "created")]


def test_backend_pass_gate_rejects_incomplete_or_unsafe_metadata() -> None:
    valid = {
        "classification": "CODEX_OAUTH_SMOKE_PASS", "auth_status": "signed_in", "oauth_bridge_ready": True,
        "execution_count": 1, "sandbox_kind": "external_disposable_oauth_smoke", "argv_shape": "global_approval_before_exec",
        "shell_false": True, "dangerous_flags_used": False, "expected_file_created": True, "expected_content_valid": True,
        "normalized_content_matches": True, "created_file_count": 1, "modified_file_count": 0, "deleted_file_count": 0,
        "marker_unchanged": True, "active_workspace_unchanged": True, "tokens_read": False, "auth_files_read": False,
        "raw_prompt_persisted": False, "raw_output_persisted": False, "production_routing_enabled": False,
        "auto_apply": False, "auto_build": False, "auto_flash": False,
    }
    assert CodexOAuthSmokeService._is_safe_pass(valid) is True
    for key in ("expected_content_valid", "normalized_content_matches", "marker_unchanged", "active_workspace_unchanged"):
        assert CodexOAuthSmokeService._is_safe_pass({**valid, key: False}) is False
    for key in ("dangerous_flags_used", "raw_prompt_persisted", "raw_output_persisted", "tokens_read", "auth_files_read"):
        assert CodexOAuthSmokeService._is_safe_pass({**valid, key: True}) is False


def test_review_rejects_extra_or_invalid_content(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"; sandbox.mkdir(); (sandbox / ".git").mkdir()
    (sandbox / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    (sandbox / SMOKE_NAME).write_text("wrong", encoding="utf-8")
    try: create_codex_oauth_smoke_review(sandbox, BridgeDiffService())
    except ValueError: pass
    else: raise AssertionError("invalid smoke content was accepted")

    (sandbox / SMOKE_NAME).write_text(SMOKE_CONTENT + "\n", encoding="utf-8")
    (sandbox / "extra.txt").write_text("extra", encoding="utf-8")
    try: create_codex_oauth_smoke_review(sandbox, BridgeDiffService())
    except ValueError: pass
    else: raise AssertionError("extra smoke file was accepted")
