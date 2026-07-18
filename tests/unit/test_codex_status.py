from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.bridges.base import DetectedExecutable
from backend.bridges.codex_status import (
    CodexAlignedStatus,
    CodexStatusService,
    buildCodexSafeUserEnv,
    codex_state_semantics,
)


class FakeRunner:
    def __init__(self, status_code: int = 0, status_output: str = "Logged in using ChatGPT") -> None:
        self.status_code = status_code
        self.status_output = status_output
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if args[-1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "codex-cli 0.142.5\n", "")
        if args[-2:] == ["login", "--help"]:
            return subprocess.CompletedProcess(args, 0, "Commands:\n  status  Show login status\n", "")
        return subprocess.CompletedProcess(args, self.status_code, self.status_output, "")


def service(tmp_path: Path, runner: FakeRunner) -> CodexStatusService:
    return CodexStatusService(
        command_runner=runner,
        env={"PATH": "C:/Tools", "USERPROFILE": "C:/Users/test", "APPDATA": "C:/Users/test/AppData/Roaming", "LOCALAPPDATA": "C:/Users/test/AppData/Local"},
        temp_root=tmp_path,
        executable=DetectedExecutable("C:/Tools/codex.exe", "codex"),
    )


def test_safe_user_env_preserves_required_windows_values() -> None:
    result = buildCodexSafeUserEnv({
        "PATH": "one", "Path": "two", "USERPROFILE": "profile",
        "APPDATA": "roaming", "LOCALAPPDATA": "local",
    })
    assert result == {"PATH": "one", "Path": "two", "USERPROFILE": "profile", "APPDATA": "roaming", "LOCALAPPDATA": "local"}


@pytest.mark.parametrize("name", [
    "OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
    "NVIDIA_API_KEY", "NIM_API_KEY", "ANTHROPIC_API_KEY", "SESSION_TOKEN",
    "CLIENT_SECRET", "LOGIN_PASSWORD", "BROWSER_COOKIE", "CREDENTIAL_STORE", "CUSTOM_AUTH",
])
def test_safe_user_env_excludes_secret_like_names(name: str) -> None:
    result = buildCodexSafeUserEnv({"PATH": "safe", name: "forbidden"})
    assert result == {"PATH": "safe"}


def test_signed_in_status_uses_direct_argv_safe_env_and_neutral_cwd(tmp_path: Path) -> None:
    runner = FakeRunner()
    result = service(tmp_path, runner).status()
    assert result.auth_status == "signed_in" and result.oauth_bridge_ready is True
    assert [call[0][-2:] for call in runner.calls] == [["C:/Tools/codex.exe", "--version"], ["login", "--help"], ["login", "status"]]
    for _, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert Path(kwargs["cwd"]).parent == tmp_path.resolve()
        assert kwargs["env"]["USERPROFILE"] == "C:/Users/test"
        assert "OPENAI_API_KEY" not in kwargs["env"]


def test_signed_out_status_is_known_nonzero(tmp_path: Path) -> None:
    result = service(tmp_path, FakeRunner(1, "Not logged in")).status()
    assert result.auth_status == "signed_out"
    assert result.exit_code_category == "nonzero"
    assert result.oauth_bridge_ready is False


@pytest.mark.parametrize(("output", "code", "expected"), [
    ("Logged in using ChatGPT", 0, "signed_in"),
    ("Not logged in", 0, "signed_out"),
    ("Login required", 1, "signed_out"),
    ("Authenticated using ChatGPT", 0, "signed_in"),
    ("", 0, "unknown"),
    ("account state unavailable", 0, "unknown"),
])
def test_status_parser_hardening(output: str, code: int, expected: str) -> None:
    assert CodexStatusService._classify_auth(code, output) == expected


def test_ambiguous_status_is_unknown(tmp_path: Path) -> None:
    result = service(tmp_path, FakeRunner(0, "Account state unavailable")).status()
    assert result.auth_status == "unknown"
    assert result.bridge_classification == "CODEX_OAUTH_BRIDGE_STATUS_UNKNOWN"


def test_status_command_unavailable_is_safe(tmp_path: Path) -> None:
    class NoStatus(FakeRunner):
        def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[-2:] == ["login", "--help"]:
                return subprocess.CompletedProcess(args, 0, "Manage login", "")
            return super().__call__(args, **kwargs)

    result = service(tmp_path, NoStatus()).status()
    assert result.auth_status == "unknown"
    assert result.auth_classification == "CODEX_AUTH_STATUS_COMMAND_UNAVAILABLE"


def test_status_timeout_is_unknown_without_raw_output(tmp_path: Path) -> None:
    class TimeoutRunner(FakeRunner):
        def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args[-2:] == ["login", "status"]:
                raise subprocess.TimeoutExpired(args, 5)
            return super().__call__(args, **kwargs)

    payload = service(tmp_path, TimeoutRunner()).status().to_safe_dict()
    assert payload["auth_status"] == "unknown"
    assert payload["exit_code_category"] == "timeout"
    assert payload["raw_output_persisted"] is False
    assert "stdout" not in payload and "stderr" not in payload


def test_provider_registry_uses_injected_shared_status(tmp_path: Path) -> None:
    class SharedStatus:
        calls = 0

        def status(self) -> CodexAlignedStatus:
            self.calls += 1
            return CodexAlignedStatus(codex_installed=True, codex_version="codex-cli 0.142.5", auth_status="signed_in", oauth_bridge_ready=True, bridge_classification="CODEX_OAUTH_BRIDGE_READY")

    shared = SharedStatus()
    entry = next(item for item in ProductProviderRegistry(env={"PROMPTFORGE_ROOT": str(tmp_path)}, codex_status_service=shared).list() if item.provider_id == "codex_cli_oauth_bridge")  # type: ignore[arg-type]
    assert shared.calls == 1
    assert entry.detected is True and entry.authenticated is True and entry.oauth_bridge_ready is True
    assert entry.product_routing_enabled is False


def test_codex_state_separates_model_router_sandbox_and_product_authority() -> None:
    payload = codex_state_semantics(
        CodexAlignedStatus(
            codex_installed=True,
            auth_status="signed_in",
            oauth_bridge_ready=True,
        ),
        model_router_enabled=True,
        sandbox_execution_enabled=True,
    )

    assert payload["codex_cli_installed"] is True
    assert payload["codex_auth_status"] == "signed_in"
    assert payload["codex_model_router_available"] is True
    assert payload["codex_model_router_enabled"] is True
    assert payload["codex_sandbox_execution_enabled"] is True
    assert payload["codex_sandbox_execution_status"] == "enabled"
    assert payload["codex_product_route_allowed"] is False
    assert payload["codex_active_workspace_mutation_allowed"] is False
    assert payload["codex_oauth_bridge_status"] == "qa_only"


def test_codex_compatibility_fields_are_derived_from_explicit_semantics() -> None:
    payload = codex_state_semantics(
        CodexAlignedStatus(codex_installed=True, auth_status="signed_in", oauth_bridge_ready=True),
        model_router_enabled=True,
        sandbox_execution_enabled=False,
    )

    assert payload["codex_provider_state"] == "product_disabled"
    assert payload["codex_routing_allowed"] == payload["codex_product_route_allowed"]
    assert payload["codex_execution_enabled"] == payload["codex_sandbox_execution_enabled"]
    assert "paused" not in payload.values()


def test_sources_keep_ui_qa_smoke_and_registry_on_shared_service() -> None:
    root = Path(__file__).resolve().parents[2]
    login = (root / "backend/bridges/codex_login.py").read_text(encoding="utf-8")
    smoke = (root / "backend/bridges/codex_oauth_smoke.py").read_text(encoding="utf-8")
    registry = (root / "backend/agent_runtime/product_provider_registry.py").read_text(encoding="utf-8")
    qa = (root / "scripts/qa-codex-oauth-bridge.mjs").read_text(encoding="utf-8")
    ui_route = (root / "backend/api/routes/agent_runtime.py").read_text(encoding="utf-8")
    assert "CodexStatusService" in login
    assert "login_service.status()" in smoke
    assert "_codex_status_service.status()" not in registry
    assert "_connections.refresh_status" in registry
    assert "getSharedAlignedStatus" in qa
    assert "_connection_registry(request).refresh_status" in ui_route
    assert "_codex_login_service(request).status()" not in ui_route


def test_diagnostics_and_parity_do_not_launch_login_or_smoke() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/qa-codex-oauth-bridge.mjs").read_text(encoding="utf-8")
    diagnostics = source[source.index("function printDiagnostics"):source.index("function printStatusParity")]
    parity = source[source.index("function printStatusParity"):source.index("async function launchLogin")]
    assert "spawn(" not in diagnostics and "runSmoke(" not in diagnostics
    assert "spawn(" not in parity and "runSmoke(" not in parity
    assert "raw_output_persisted = false" in diagnostics
    assert "process.env" not in diagnostics
