"""Regression checks for the Phase 0 build and store boundaries."""

from configparser import RawConfigParser
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.security.build_policy import validate_build_configuration
from backend.tools.build_firmware import BuildConfig, FirmwareBuilder, ProjectValidationError


@pytest.mark.parametrize(
    "option,value",
    [
        ("platform", "https://example.invalid/platform.git"),
        ("platform", "file:../platform"),
        ("board", "../../board"),
        ("lib_deps", "https://example.invalid/library.git"),
        ("lib_deps", "../local-library"),
    ],
)
def test_build_policy_rejects_executable_package_sources(option: str, value: str) -> None:
    parser = RawConfigParser()
    parser.read_string(f"[env:test]\n{option} = {value}\n")
    with pytest.raises(ValueError, match="BUILD_CONFIGURATION_REQUIRES_TRUST"):
        validate_build_configuration(parser)


def test_build_policy_accepts_registry_packages() -> None:
    parser = RawConfigParser()
    parser.read_string(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n"
        "lib_deps = adafruit/Adafruit SSD1306@^2.5.13\n"
        "  bblanchon/ArduinoJson@^7.0.0\n"
    )
    validate_build_configuration(parser)


def test_store_releases_lock_after_connect_failure(tmp_path, monkeypatch) -> None:
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    connect = store._connect

    def fail_connect():
        raise OSError("cannot open database")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(OSError, match="cannot open database"):
        with store._transaction():
            pass
    monkeypatch.setattr(store, "_connect", connect)
    def probe_lock() -> bool:
        acquired = store._lock.acquire(timeout=1)
        if acquired:
            store._lock.release()
        return acquired

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(probe_lock).result(timeout=2)
    with store._transaction():
        with pytest.raises(RuntimeError, match="Nested"):
            with store._transaction():
                pass
    store.save_run("run-1", "session-1", "project-1", "completed", {"ok": True})


def test_builder_rejects_project_root_different_from_approved_path(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n",
        encoding="utf-8",
    )
    with pytest.raises(ProjectValidationError, match="changed after workspace authorization"):
        FirmwareBuilder._validate_project(
            BuildConfig(project_dir=project, expected_project_root=tmp_path / "other")
        )
