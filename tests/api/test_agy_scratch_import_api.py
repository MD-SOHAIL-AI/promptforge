from __future__ import annotations

from pathlib import Path

import pytest

from backend.bridges.agy_scratch_project_import import AGYScratchProjectImportService
from tests.api.test_antigravity_bridge_routes import make_client


def configure_service(api, tmp_path: Path) -> tuple[Path, Path]:
    app_root = tmp_path / "app-root"
    scratch = tmp_path / "home" / ".gemini" / "antigravity-cli" / "scratch"
    scratch.mkdir(parents=True)
    active = app_root / "workspace"
    active.mkdir(parents=True, exist_ok=True)
    (active / "ACTIVE.txt").write_text("unchanged\n", encoding="utf-8")
    api.app.state.agy_scratch_import_service = AGYScratchProjectImportService(
        repository_root=app_root,
        active_workspace_root=active,
        managed_sandbox_root=app_root / ".promptforge" / "agy-import-sandboxes",
        review_service=api.app.state.bridge_diff_service,
        scratch_root=scratch,
        status_path=app_root / ".promptforge" / "state" / "agy-scratch-project-import-status.json",
        env={},
    )
    return scratch, active


def test_route_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "0")
    with make_client(tmp_path, enabled=False) as api:
        response = api.post("/agent-runtime/agy-scratch-import", json={"source_path": "C:\\not-used"})
    assert response.status_code == 403
    assert response.json()["code"] == "AGY_SCRATCH_IMPORT_DISABLED"


def test_route_imports_exact_selected_folder_and_creates_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        scratch, active = configure_service(api, tmp_path)
        source = scratch / "esp32_blink"
        (source / "src").mkdir(parents=True)
        (source / "platformio.ini").write_text("[env:esp32dev]\n", encoding="utf-8")
        (source / "src" / "main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
        before = (active / "ACTIVE.txt").read_bytes()

        response = api.post("/agent-runtime/agy-scratch-import", json={"source_path": str(source)})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["classification"] == "AGY_SCRATCH_IMPORT_PASS"
        assert body["created_file_count"] == 2
        assert body["review_created"] is True and body["review_id"]
        assert body["active_workspace_unchanged"] is True
        assert str(source) not in response.text
        review = api.get(f"/models/bridges/reviews/{body['review_id']}")
        assert review.status_code == 200
        assert review.json()["review"]["provider_id"] == "agy_scratch_import"
        assert review.json()["review"]["artifact_metadata"]["source_name"] == "esp32_blink"
        assert (active / "ACTIVE.txt").read_bytes() == before


def test_route_returns_sanitized_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        scratch, _ = configure_service(api, tmp_path)
        source = scratch / "unsafe"
        source.mkdir()
        (source / ".env").write_text("SECRET=value\n", encoding="utf-8")
        response = api.post("/agent-runtime/agy-scratch-import", json={"source_path": str(source)})
    assert response.status_code == 422
    assert response.json()["code"] == "AGY_SCRATCH_SECRET_FILE_BLOCKED"
    assert str(source) not in response.text


def test_provider_status_is_manual_nonproduction_and_not_planner_routeable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "1")
    with make_client(tmp_path, enabled=False) as api:
        providers = api.get("/agent-runtime/providers").json()["providers"]
    item = next(value for value in providers if value["provider_id"] == "agy_scratch_import")
    assert item["provider_kind"] == "manual_artifact"
    assert item["execution_mode"] == "manual_import"
    assert item["workspace_mode"] == "managed_import_sandbox"
    assert item["auth_mode"] == "none"
    assert item["qa_only"] is False
    assert item["production_eligible"] is False
    assert item["routeable"] is False
