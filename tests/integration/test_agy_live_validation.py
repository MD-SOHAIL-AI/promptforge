from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.api.schemas.generic_runs import GenericRunStartRequest


ROOT = Path(__file__).parents[2]
LIVE_SCRIPT = ROOT / "scripts" / "qa-agy-generic-live.mjs"
CORE_SCRIPT = ROOT / "scripts" / "qa-agy-generic-live-core.mjs"


def test_agy_live_check_only_blocks_missing_flags_without_real_execution() -> None:
    env = os.environ.copy()
    for name in (
        "FORGEX_QA_MODE",
        "FORGEX_ENABLE_AGY_BRIDGE",
        "FORGEX_ENABLE_GENERIC_BRIDGE_API",
        "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
        "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
        "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
        "FORGEX_AGY_AUTHENTICATED",
    ):
        env.pop(name, None)
    result = subprocess.run(
        ["node", str(LIVE_SCRIPT), "--check-only"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        shell=False,
    )
    assert result.returncode == 2
    assert result.stdout.splitlines()[0] == "BLOCKED_FLAGS_MISSING"
    evidence = json.loads((ROOT / ".promptforge" / "state" / "phase-2-5-8-6-live-agy-validation.json").read_text())
    assert evidence["real_execution_count"] == 0


def test_agy_live_missing_confirmation_blocks_execution() -> None:
    source = LIVE_SCRIPT.read_text(encoding="utf-8")
    assert 'if (!confirmed) finish("BLOCKED_OPERATOR_DID_NOT_AUTHORIZE"' in source
    assert "--confirm-real-agy" in source


def test_agy_live_requires_qa_mode_marker_and_all_generic_flags() -> None:
    core = CORE_SCRIPT.read_text(encoding="utf-8")
    for name in (
        "FORGEX_QA_MODE",
        "FORGEX_ENABLE_AGY_BRIDGE",
        "FORGEX_ENABLE_GENERIC_BRIDGE_API",
        "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
        "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
        "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
        "QA_NOT_REAL_PROJECT.txt",
    ):
        assert name in core or name in LIVE_SCRIPT.read_text(encoding="utf-8")


def test_agy_live_instruction_is_runtime_only_and_runner_writes_no_prompt_file() -> None:
    runner = (ROOT / "backend" / "bridges" / "providers" / "antigravity_runner.py").read_text(encoding="utf-8")
    persistence = (ROOT / "backend" / "bridges" / "generic" / "persistence.py").read_text(encoding="utf-8")
    assert "prompt_path" not in runner
    assert 'args = [executable, "-p", prompt]' in runner
    assert "request.instruction," not in persistence


def test_agy_live_request_schema_rejects_invalid_provider_blank_and_oversized_instruction() -> None:
    base = {
        "provider_id": "agy",
        "project_id": "qa-project-live",
        "instruction": "safe",
        "timeout_seconds": 10,
        "idempotency_key": "agy-live-test-001",
    }
    for mutation in (
        {"provider_id": "codex"},
        {"instruction": "   "},
        {"instruction": "x" * 16_385},
    ):
        with pytest.raises(ValueError):
            GenericRunStartRequest(**(base | mutation))


def test_agy_live_has_no_legacy_fallback_or_automatic_actions() -> None:
    source = LIVE_SCRIPT.read_text(encoding="utf-8")
    assert 'legacy_fallback: false' in source
    assert "automatic_apply: false" in source
    assert "automatic_build: false" in source
    assert "automatic_flash: false" in source
    assert "fallback" not in (ROOT / "backend" / "bridges" / "agy_execution_router.py").read_text(encoding="utf-8").casefold()


def test_agy_live_supports_safe_cancellation_timeout_and_review_preflight() -> None:
    source = LIVE_SCRIPT.read_text(encoding="utf-8")
    for marker in ("--cancel-smoke", "--timeout-smoke", "BLOCKED_FAST_COMPLETION", "export-patch", "verify-patch", "/preflight"):
        assert marker in source


def test_agy_live_never_uses_shell_true_or_expands_provider_support() -> None:
    combined = LIVE_SCRIPT.read_text(encoding="utf-8") + CORE_SCRIPT.read_text(encoding="utf-8")
    assert "shell: true" not in combined
    assert 'provider_id: "agy"' in combined
    assert 'provider_id: "codex"' not in combined
    assert 'provider_id: "claude"' not in combined
    assert 'provider_id: "opencode"' not in combined
