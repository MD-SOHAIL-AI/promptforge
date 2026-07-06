from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.bridges import BridgeDetectionService
from backend.bridges.codex_login import CodexLoginService
from backend.bridges.codex_oauth_smoke import CodexOAuthSmokeService
from backend.bridges.providers.codex import CodexDetector
from backend.model_router import ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.services.code_generation_service import CodeGenerationService
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.project_service import ProjectService


class MissingToolRunner:
    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("shell") is False
        return subprocess.CompletedProcess(list(args), 1, "", "")


def client(tmp_path: Path, *, codex_login_service: CodexLoginService | None = None, codex_oauth_smoke_service: CodexOAuthSmokeService | None = None) -> TestClient:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {})
    generation = CodeGenerationService(
        router,
        diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"),
    )
    app = create_app(
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        bridge_detection_service=BridgeDetectionService(
            command_runner=MissingToolRunner(),
            platform_name="Linux",
        ),
        codex_login_service=codex_login_service,
        codex_oauth_smoke_service=codex_oauth_smoke_service,
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False)


def test_bridge_route_returns_all_three_detection_cards(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.get("/models/bridges")

    assert response.status_code == 200
    payload = response.json()
    assert [bridge["provider_id"] for bridge in payload["bridges"]] == [
        "codex_cli_oauth_bridge",
        "claude_code_bridge",
        "antigravity_cli_bridge",
    ]
    assert "gemini_cli_bridge" not in [bridge["provider_id"] for bridge in payload["bridges"]]
    assert payload["bridges"][2]["display_name"] == "Google Antigravity / AGY CLI"
    assert all(bridge["capabilities"]["run_prompt"] is False for bridge in payload["bridges"])
    assert all(bridge["can_run"] is False for bridge in payload["bridges"])
    assert all(bridge["status_confidence"] in {"high", "medium", "low"} for bridge in payload["bridges"])
    assert all("safe_status_checked" in bridge for bridge in payload["bridges"])


def test_bridge_refresh_and_detail_routes(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        refreshed = api.post("/models/bridges/refresh")
        detail = api.get("/models/bridges/codex_cli_oauth_bridge")
        missing = api.get("/models/bridges/not_a_bridge")

    assert refreshed.status_code == 200
    assert len(refreshed.json()["bridges"]) == 3
    assert detail.status_code == 200
    assert detail.json()["bridge"]["provider_id"] == "codex_cli_oauth_bridge"
    assert missing.status_code == 404


def test_antigravity_bridge_detail_route(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        detail = api.get("/models/bridges/antigravity_cli_bridge")
        legacy = api.get("/models/bridges/gemini_cli_bridge")

    assert detail.status_code == 200
    assert detail.json()["bridge"]["provider_id"] == "antigravity_cli_bridge"
    assert legacy.status_code == 404


def test_codex_oauth_routes_require_confirmation_and_return_safe_fields(tmp_path: Path) -> None:
    runner = MissingToolRunner()
    detector = CodexDetector(command_runner=runner, platform_name="Linux")
    launched: list[tuple[object, object]] = []
    service = CodexLoginService(
        detector=detector,
        popen_factory=lambda args, **kwargs: launched.append((args, kwargs)),
        temp_root=tmp_path,
    )

    with client(tmp_path, codex_login_service=service) as api:
        status = api.get("/agent-runtime/providers/codex-oauth/status")
        refused = api.post("/agent-runtime/providers/codex-oauth/login/launch", json={})

    assert status.status_code == 200
    assert status.json()["provider_id"] == "codex_cli_oauth_bridge"
    assert status.json()["tokens_read"] is False
    assert status.json()["auth_files_read"] is False
    assert refused.status_code == 200
    assert refused.json()["classification"] == "CODEX_LOGIN_CONFIRMATION_REQUIRED"
    assert refused.json()["launched"] is False
    assert launched == []


def test_codex_oauth_smoke_route_requires_confirmation_and_returns_sanitized_pass(tmp_path: Path) -> None:
    class ReadyDetector:
        def detect(self):
            return SimpleNamespace(installed=True, version="codex-cli 0.142.5", auth_status="authenticated",
                                   checked_commands=("codex --version", "codex login --help", "codex login status"))

    login = CodexLoginService(detector=ReadyDetector())  # type: ignore[arg-type]
    output = "\n".join([
            "classification = CODEX_OAUTH_SMOKE_PASS", "provider_id = codex_cli_oauth_bridge",
            "auth_status = signed_in", "oauth_bridge_ready = true", "execution_count = 1",
            "sandbox_kind = external_disposable_oauth_smoke", "argv_shape = global_approval_before_exec",
            "shell_false = true", "dangerous_flags_used = false", "review_created = true",
            "review_id = bridge-review-route", "created_file_count = 1", "modified_file_count = 0",
            "deleted_file_count = 0", "expected_file_created = true", "expected_content_valid = true",
            "normalized_content_matches = true", "marker_unchanged = true", "active_workspace_unchanged = true",
            "production_routing_enabled = false", "tokens_read = false", "auth_files_read = false",
            "raw_prompt_persisted = false", "raw_output_persisted = false",
            "auto_apply = false", "auto_build = false", "auto_flash = false",
        ])
    smoke = CodexOAuthSmokeService(
        login_service=login, repository_root=tmp_path, feature_enabled=True,
        command_runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, "secret raw stderr"),
    )
    with client(tmp_path, codex_login_service=login, codex_oauth_smoke_service=smoke) as api:
        refused = api.post("/agent-runtime/providers/codex-oauth/standalone-smoke", json={})
        passed = api.post("/agent-runtime/providers/codex-oauth/standalone-smoke", json={"confirm_real_codex": True})
    assert refused.json()["classification"] == "CODEX_OAUTH_SMOKE_CONFIRMATION_REQUIRED"
    assert passed.json()["classification"] == "CODEX_OAUTH_SMOKE_PASS"
    assert passed.json()["review_id"] == "bridge-review-route"
    assert "stdout" not in passed.json() and "stderr" not in passed.json()
