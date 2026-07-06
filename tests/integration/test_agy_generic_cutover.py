from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.bridges.agy_execution_router import AGYCutoverFlags, AGYExecutionMode, legacy_status_for
from backend.bridges.agy_trusted_workspace import TRUSTED_WORKSPACE_MARKER
from backend.bridges.generic import BridgeRunStatus
from tests.api.test_antigravity_bridge_routes import make_client, wait_for_status, write


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (AGYCutoverFlags(False, False, False, False), AGYExecutionMode.BLOCKED),
        (AGYCutoverFlags(False, True, True, True), AGYExecutionMode.BLOCKED),
        (AGYCutoverFlags(True, False, False, False), AGYExecutionMode.LEGACY),
        (AGYCutoverFlags(True, True, False, True), AGYExecutionMode.BLOCKED),
        (AGYCutoverFlags(True, True, True, False), AGYExecutionMode.LEGACY),
        (AGYCutoverFlags(True, False, True, True), AGYExecutionMode.BLOCKED),
        (AGYCutoverFlags(True, True, True, True), AGYExecutionMode.GENERIC),
    ],
)
def test_agy_generic_cutover_feature_matrix(flags: AGYCutoverFlags, expected: AGYExecutionMode) -> None:
    assert flags.effective_mode() is expected


@pytest.mark.parametrize(
    ("canonical", "legacy"),
    [
        (BridgeRunStatus.QUEUED, "pending"),
        (BridgeRunStatus.VALIDATING, "pending"),
        (BridgeRunStatus.PREPARING_SANDBOX, "pending"),
        (BridgeRunStatus.RUNNING, "running"),
        (BridgeRunStatus.COLLECTING_ARTIFACTS, "running"),
        (BridgeRunStatus.COMPLETED, "review_ready"),
        (BridgeRunStatus.BLOCKED, "failed"),
        (BridgeRunStatus.FAILED, "failed"),
        (BridgeRunStatus.CANCELLING, "running"),
        (BridgeRunStatus.CANCELLED, "cancelled"),
        (BridgeRunStatus.TIMED_OUT, "failed_timeout"),
        (BridgeRunStatus.INTERRUPTED, "failed"),
    ],
)
def test_agy_generic_cutover_state_compatibility(canonical: BridgeRunStatus, legacy: str) -> None:
    assert legacy_status_for(canonical) == legacy
    assert legacy_status_for(canonical.value) == legacy


def test_agy_generic_cutover_unknown_state_fails_safely() -> None:
    assert legacy_status_for("future-state") == "failed"


def enable_cutover(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_GENERIC_PROVIDER", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_GENERIC_CUTOVER", "1")


def test_agy_generic_cutover_fake_e2e_exactly_once_and_review_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_cutover(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace" / "forgex-apply-test"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True) as api:
        status = api.get("/models/bridges/antigravity/sandbox-status").json()
        first = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={
                "workspace_root": str(workspace),
                "prompt": "change readme",
                "idempotency_key": "cutover-e2e-001",
            },
        )
        repeated = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={
                "workspace_root": str(workspace),
                "prompt": "change readme",
                "idempotency_key": "cutover-e2e-001",
            },
        )
        run_id = first.json()["run"]["run_id"]
        completed = wait_for_status(api, run_id)
        review = api.get(f"/models/bridges/reviews/{completed['review_id']}")
        router = api.app.state.agy_execution_router
        runner = api.app.state.bridge_agy_runner
        audit_entries = router.audit_log.list_entries(200)
        persisted_record = api.app.state.generic_bridge_coordinator.get_run(run_id)

    assert status["execution_mode"] == "generic"
    assert first.status_code == 200 and repeated.status_code == 200
    assert repeated.json()["run"]["run_id"] == run_id
    assert completed["status"] == "review_ready"
    assert completed["sandbox_root"] == ""
    assert completed["changed_file_count"] == 1
    assert review.status_code == 200
    assert review.json()["review"]["provider_id"] == "antigravity_cli_bridge"
    assert len(runner.popen_factory.calls) == 1
    assert len([item for item in runner.list_runs() if item.run_id == run_id]) == 1
    sandbox_children = [item for item in router.sandbox_service.sandbox_root.iterdir() if item.is_dir()]
    assert len(sandbox_children) == 1
    assert persisted_record.execution_mode == "generic"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "base\n"
    assert any(entry["event"] == "agy_execution_routed" for entry in audit_entries)
    assert any(entry["event"] == "agy_execution_terminal" for entry in audit_entries)
    serialized_audit = json.dumps(audit_entries)
    assert "change readme" not in serialized_audit
    assert str(workspace) not in serialized_audit


def test_agy_generic_cutover_failure_never_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_cutover(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace" / "forgex-apply-test"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True) as api:
        runner = api.app.state.bridge_agy_runner
        runner.popen_factory.process.returncode = 1
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "fail safely"},
        )
        completed = wait_for_status(api, response.json()["run"]["run_id"])

    assert completed["status"] == "failed"
    assert len(runner.popen_factory.calls) == 1
    assert completed["review_id"] is None
    assert (workspace / "README.md").read_text(encoding="utf-8") == "base\n"


def test_agy_generic_cutover_partial_flags_fail_closed_before_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING", "1")
    monkeypatch.delenv("FORGEX_ENABLE_AGY_GENERIC_PROVIDER", raising=False)
    monkeypatch.setenv("FORGEX_ENABLE_AGY_GENERIC_CUTOVER", "1")
    workspace = tmp_path / "workspace" / "forgex-apply-test"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True) as api:
        router = api.app.state.agy_execution_router
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "must not run"},
        )

    assert response.status_code == 403
    assert not router.sandbox_service.sandbox_root.exists()


def test_agy_trusted_workspace_mode_reuses_cwd_and_keeps_active_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enable_cutover(monkeypatch, tmp_path)
    monkeypatch.setenv("FORGEX_QA_MODE", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE", "1")
    workspace = tmp_path / "workspace" / "forgex-apply-test"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True) as api:
        trusted = api.app.state.agy_trusted_workspace_service.prepare(workspace)
        runner = api.app.state.bridge_agy_runner
        process = runner.popen_factory.process
        def create_smoke(timeout=None):
            assert process.cwd is not None
            write(process.cwd / "AGY_GENERIC_SMOKE.txt", "ForgeX generic AGY smoke test completed.\n")
            return "ok", ""
        process.communicate = create_smoke
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "runtime only"},
        )
        completed = wait_for_status(api, response.json()["run"]["run_id"])
        review = api.get(f"/models/bridges/reviews/{completed['review_id']}").json()["review"]

    assert response.status_code == 200
    assert completed["status"] == "review_ready"
    assert Path(runner.popen_factory.calls[0][1]["cwd"]) == trusted
    assert workspace.joinpath("README.md").read_text(encoding="utf-8") == "base\n"
    assert [item["path"] for item in review["changed_files"]] == ["AGY_GENERIC_SMOKE.txt"]
    assert TRUSTED_WORKSPACE_MARKER not in [item["path"] for item in review["changed_files"]]
    assert not list(api.app.state.agy_trusted_workspace_service.managed_root.glob("*.lock"))
