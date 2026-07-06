from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from backend.bridges.agy_trusted_workspace import (
    LOCK_STALE_SECONDS,
    TRUSTED_WORKSPACE_MARKER,
    AGYTrustedWorkspaceService,
)
from backend.bridges.sandbox_service import BridgeSandboxError


def write(path: Path, value: str = "base\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def service_fixture(tmp_path: Path, *, enabled: bool = True) -> tuple[AGYTrustedWorkspaceService, Path]:
    active = tmp_path / "active"
    write(active / "README.md")
    env = {"FORGEX_QA_MODE": "1", "FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE": "1"} if enabled else {}
    return AGYTrustedWorkspaceService(tmp_path / "state" / "agy-trusted-workspaces", env=env), active


def test_trusted_workspace_creation_marker_containment_and_reset(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    write(active / "src" / "main.cpp", "code\n")
    write(active / "node_modules" / "pkg" / "index.js", "ignored\n")
    workspace = service.prepare(active)
    write(workspace / "stale.txt")

    lease = service.acquire_and_reset(active)
    try:
        assert lease.workspace_root == workspace
        assert (workspace / TRUSTED_WORKSPACE_MARKER).is_file()
        assert (workspace / "src" / "main.cpp").read_text(encoding="utf-8") == "code\n"
        assert not (workspace / "stale.txt").exists()
        assert not (workspace / "node_modules").exists()
        assert workspace.parent == service.managed_root
        assert workspace != active
    finally:
        service.release(lease)


def test_trusted_workspace_requires_qa_and_explicit_flag(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path, enabled=False)
    service.prepare(active)
    with pytest.raises(BridgeSandboxError, match="disabled"):
        service.acquire_and_reset(active)


def test_trusted_workspace_marker_is_required(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    workspace = service.prepare(active)
    (workspace / TRUSTED_WORKSPACE_MARKER).unlink()
    with pytest.raises(BridgeSandboxError, match="marker"):
        service.verify(active)


def test_trusted_workspace_rejects_active_repo_and_sensitive_roots(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    service.prepare(active)
    malicious = AGYTrustedWorkspaceService(active.parent, env=service.env)
    malicious.workspace_for = lambda _active: active  # type: ignore[method-assign]
    with pytest.raises(BridgeSandboxError, match="protected root"):
        malicious.verify(active)


def test_trusted_workspace_rejects_symlink_escape(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    workspace = service.prepare(active)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(BridgeSandboxError, match="symbolic link"):
        service.verify(active)


def test_trusted_workspace_lock_prevents_concurrent_runs(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    service.prepare(active)
    first = service.acquire_and_reset(active)
    try:
        with pytest.raises(BridgeSandboxError, match="already in use"):
            service.acquire_and_reset(active)
    finally:
        service.release(first)


def test_trusted_workspace_stale_lock_recovers_only_when_old_and_dead(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    workspace = service.prepare(active)
    lock = service.managed_root / f"{workspace.name}.lock"
    lock.write_text(json.dumps({"pid": 999_999_999, "created": int(time.time()) - LOCK_STALE_SECONDS - 5}), encoding="utf-8")
    lease = service.acquire_and_reset(active)
    service.release(lease)
    lock.write_text("invalid", encoding="utf-8")
    with pytest.raises(BridgeSandboxError, match="already in use"):
        service.acquire_and_reset(active)


def test_trusted_workspace_reset_refuses_symlink_before_deletion(tmp_path: Path) -> None:
    service, active = service_fixture(tmp_path)
    workspace = service.prepare(active)
    outside = tmp_path / "outside.txt"
    write(outside, "protected\n")
    try:
        (workspace / "escape.txt").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(BridgeSandboxError, match="symbolic link"):
        service.acquire_and_reset(active)
    assert outside.read_text(encoding="utf-8") == "protected\n"
