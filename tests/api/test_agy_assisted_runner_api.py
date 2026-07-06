from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.agy_assisted_runner import AGYAssistedRunner
from backend.bridges.agy_scratch_project_import import AGYScratchProjectImportService
from tests.api.test_antigravity_bridge_routes import make_client
from tests.unit.test_agy_assisted_runner import FakeAGY


def configure_runner(api, tmp_path: Path, *, mode: str = "pass") -> None:
    app_root = tmp_path / "app-root"
    active = app_root / "workspace"
    scratch = tmp_path / "home" / ".gemini" / "antigravity-cli" / "scratch"
    active.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    (active / "ACTIVE.txt").write_text("unchanged\n", encoding="utf-8")
    importer = AGYScratchProjectImportService(
        repository_root=app_root,
        active_workspace_root=active,
        managed_sandbox_root=app_root / ".promptforge" / "agy-import-sandboxes",
        review_service=api.app.state.bridge_diff_service,
        scratch_root=scratch,
        status_path=app_root / ".promptforge" / "state" / "agy-scratch-project-import-status.json",
        env={},
    )
    api.app.state.agy_assisted_runner = AGYAssistedRunner(
        repository_root=app_root,
        active_workspace_root=active,
        import_service=importer,
        invocation_root=tmp_path / "external-runs",
        feature_enabled=True,
        process_runner=FakeAGY(scratch, mode),
        executable_resolver=lambda command: "C:/tools/agy.exe" if command == "agy" else None,
        status_path=app_root / ".promptforge" / "state" / "agy-assisted-runner-status.json",
        env={},
        home_root=tmp_path / "unrelated-home",
    )


def test_assisted_route_disabled_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "0")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "0")
    with make_client(tmp_path, enabled=False) as api:
        response = api.post("/agent-runtime/agy-assisted-runs", json={"template": "esp32-platformio-blink", "prompt": "Create an ESP32 blink project"})
    assert response.status_code == 403
    assert response.json()["code"] == "AGY_ASSISTED_RUNNER_DISABLED"


def test_assisted_route_rejects_arbitrary_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        response = api.post("/agent-runtime/agy-assisted-runs", json={"template": "esp32-platformio-blink", "prompt": "arbitrary command"})
    assert response.status_code == 422


def test_assisted_route_creates_review_from_exact_expected_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        configure_runner(api, tmp_path)
        response = api.post("/agent-runtime/agy-assisted-runs", json={"template": "esp32-platformio-blink", "prompt": "Create an ESP32 blink project"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["classification"] == "AGY_ASSISTED_IMPORT_PASS"
        assert body["expected_folder_found"] is True
        assert body["active_workspace_unchanged"] is True
        assert body["review_id"]
        review = api.get(f"/models/bridges/reviews/{body['review_id']}")
        assert review.status_code == 200
        assert review.json()["review"]["provider_id"] == "agy_scratch_runner"


def test_assisted_route_reports_manual_fallback_without_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "1")
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        configure_runner(api, tmp_path, mode="missing")
        response = api.post("/agent-runtime/agy-assisted-runs", json={"template": "esp32-platformio-blink", "prompt": "Create an ESP32 blink project"})
    assert response.status_code == 200
    body = response.json()
    assert body["classification"] == "AGY_ASSISTED_EXPECTED_FOLDER_MISSING"
    assert body["manual_import_fallback_available"] is True
    assert body["review_created"] is False


def test_ui_contains_missing_folder_manual_fallback_without_raw_output() -> None:
    source = (Path(__file__).resolve().parents[2] / "frontend" / "components" / "ide" / "product-agent-panel.tsx").read_text(encoding="utf-8")
    assert "Generate with AGY" in source
    assert "Run AGY and Create Review" in source
    assert "AGY generated output, but not at the expected folder" in source
    assert "importAGYScratchProject" in source
    assert "stdout" not in source and "stderr" not in source
