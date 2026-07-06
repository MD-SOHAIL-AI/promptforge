from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.bridges import AntigravitySandboxRunner, BridgeDetectionService, BridgeDiffService, BridgeSandboxService
from backend.bridges.audit_log import BridgeAuditLog
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.generic import (
    CapabilityStatus,
    ProviderCapabilityEvidence,
    ProviderCapabilityRegistry,
    ProviderKind,
    ProviderOperationalState,
    TransportMode,
)
from backend.model_router import ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.services.code_generation_service import CodeGenerationService
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.project_service import ProjectService


class MissingToolRunner:
    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 1, "", "")


class FakeProcess:
    pid = 24680
    returncode = 0

    def __init__(self) -> None:
        self.cwd: Path | None = None
        self.killed = False

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        if self.cwd is not None:
            (self.cwd / "README.md").write_text("changed in sandbox\n", encoding="utf-8")
        return "ok", ""

    def poll(self) -> int | None:
        return None if not self.killed else -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class RecordingPopen:
    def __init__(self) -> None:
        self.process = FakeProcess()
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((args, kwargs))
        self.process.cwd = Path(kwargs["cwd"])
        return self.process


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_client(tmp_path: Path, *, enabled: bool, resolver=lambda command: "agy") -> TestClient:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {})
    generation = CodeGenerationService(
        router,
        diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"),
    )
    audit = BridgeAuditLog(tmp_path / "bridge-audit.jsonl")
    review_service = BridgeDiffService(
        audit_log=audit,
        store=BridgeReviewStore(
            snapshots_path=tmp_path / "bridge-snapshots.jsonl",
            reviews_path=tmp_path / "bridge-reviews.jsonl",
        ),
    )
    runner = AntigravitySandboxRunner(
        sandbox_service=BridgeSandboxService(tmp_path / "bridge-sandboxes"),
        review_service=review_service,
        audit_log=audit,
        executable_resolver=resolver,
        popen_factory=RecordingPopen(),
        feature_enabled=enabled,
    )
    fake_strategy = ProviderCapabilityRegistry(
        (
            ProviderCapabilityEvidence(
                provider_id="agy",
                display_name="Fake AGY",
                provider_kind=ProviderKind.LOCAL_SERVER,
                transport_modes=(TransportMode.CLI_HEADLESS,),
                detected=True,
                headless_mode_available=True,
                auth_status=CapabilityStatus.PASSED,
                permission_status=CapabilityStatus.PASSED,
                native_write_status=CapabilityStatus.PASSED,
                safety_scan_status=CapabilityStatus.PASSED,
                operational_state=ProviderOperationalState.ACTIVE,
                production_eligible=True,
                evidence_source="fake_test_provider",
            ),
        )
    )
    app = create_app(
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        bridge_detection_service=BridgeDetectionService(
            command_runner=MissingToolRunner(),
            platform_name="Linux",
        ),
        bridge_diff_service=review_service,
        bridge_agy_runner=runner,
        provider_capability_registry=fake_strategy,
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False)


def wait_for_status(api: TestClient, run_id: str) -> dict[str, Any]:
    for _ in range(100):
        response = api.get(f"/models/bridges/runs/{run_id}")
        payload = response.json()["run"]
        if payload["status"] not in {"pending", "running"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("run did not complete")


def test_antigravity_sandbox_route_disabled_without_feature_flag(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=False) as api:
        status = api.get("/models/bridges/antigravity/sandbox-status")
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "change readme"},
        )

    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert response.status_code == 403


def test_antigravity_sandbox_route_requires_installed_agy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True, resolver=lambda command: None) as api:
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "change readme"},
        )

    assert response.status_code == 422


def test_antigravity_sandbox_route_creates_review_without_changing_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")

    with make_client(tmp_path, enabled=True) as api:
        response = api.post(
            "/models/bridges/antigravity/sandbox-run",
            json={"workspace_root": str(workspace), "prompt": "change readme"},
        )
        assert response.status_code == 200
        run_id = response.json()["run"]["run_id"]
        run = wait_for_status(api, run_id)
        review = api.get(f"/models/bridges/reviews/{run['review_id']}")
        approved = api.post(f"/models/bridges/reviews/{run['review_id']}/approve")

    assert run["status"] == "review_ready"
    assert run["changed_file_count"] == 1
    assert review.status_code == 200
    assert review.json()["review"]["provider_id"] == "antigravity_cli_bridge"
    assert approved.json()["review"]["status"] == "approved"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "base\n"
