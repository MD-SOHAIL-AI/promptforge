from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "scripts" / "qa-agy-native-write.mjs"
CORE = ROOT / "scripts" / "qa-agy-native-write-core.mjs"


def test_agy_native_write_refuses_without_confirmation() -> None:
    result = subprocess.run(["node", str(COMMAND)], cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 2
    assert "NATIVE_UNSAFE_ABORTED" in result.stdout
    assert "explicit_confirmation_required" in result.stdout


def test_agy_native_write_refuses_without_trust_attestation() -> None:
    result = subprocess.run(["node", str(COMMAND), "--confirm-native-agy"], cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 2
    assert "trusted_workspace_attestation_required" in result.stdout
    assert '"execution_count":0' in result.stdout


def test_agy_native_write_uses_guarded_workspace_and_direct_argv() -> None:
    source = COMMAND.read_text(encoding="utf-8")
    assert all(marker in source for marker in ("guardActiveWorkspace", "guardTrustedWorkspace", "prepareTrustedWorkspace"))
    assert "spawnSync(executable, args" in source
    assert "shell: false" in source
    assert "shell: true" not in source


def test_agy_native_write_blocks_dangerous_permission_bypass() -> None:
    source = CORE.read_text(encoding="utf-8")
    assert "--dangerously-skip-permissions" in source
    assert "dangerous_permission_flag_rejected" in source


def test_agy_native_write_output_is_sanitized_and_not_persisted() -> None:
    source = COMMAND.read_text(encoding="utf-8")
    assert "sanitizedNativeResult" in source
    assert "writeFileSync" not in source
    assert "appendFileSync" not in source


def test_agy_native_write_has_no_other_provider_or_automatic_action() -> None:
    source = COMMAND.read_text(encoding="utf-8").casefold()
    for forbidden in ("codex", "claude", "opencode", "auto_apply", "auto_build", "auto_flash"):
        assert forbidden not in source


def test_agy_native_auth_check_has_no_write_instruction_path() -> None:
    source = COMMAND.read_text(encoding="utf-8")
    assert "--auth-check-only" in source
    auth_branch = source[source.index("function runAuthCheckOnly"):]
    assert "nativeInstruction(" not in auth_branch
    assert '["--version"]' in auth_branch
    assert "write_execution_count: 0" in auth_branch


def test_agy_native_auth_check_does_not_read_credentials() -> None:
    source = COMMAND.read_text(encoding="utf-8").casefold()
    for forbidden in ("credential", "cookie", "oauth", "token file", "readfile", "homedir"):
        assert forbidden not in source[source.index("function runauthcheckonly"):]
