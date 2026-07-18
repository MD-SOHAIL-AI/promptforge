from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.test_coding_workflow_routes import BASE, configure_env, make_route_rig, make_workspace


FILES_ROUTE = f"{BASE}/context/files"


class BlockingModelRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_model(self, request: object) -> object:
        self.calls += 1
        raise AssertionError("context file listing must not call model_router")


def file_list_workspace(tmp_path: Path) -> Path:
    active = make_workspace(tmp_path)
    (active / "platformio.ini").write_text("[env:esp32dev]\nframework = arduino\n", encoding="utf-8")
    (active / "README.md").write_text("# Demo\n", encoding="utf-8")
    (active / "src").mkdir(exist_ok=True)
    (active / "src" / "main.cpp").write_text("#include <Arduino.h>\nvoid setup() {}\n", encoding="utf-8")
    (active / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    (active / ".pio").mkdir()
    (active / ".pio" / "build.log").write_text("raw generated log\n", encoding="utf-8")
    return active


def test_context_files_route_disabled_when_unified_flag_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=False, fake=False)
    rig = make_route_rig(tmp_path)
    active = file_list_workspace(tmp_path)

    response = rig.api.post(FILES_ROUTE, json={"workspace_path": str(active)})

    assert response.status_code == 403
    assert response.json()["code"] == "UNIFIED_CODING_WORKFLOW_DISABLED"


def test_context_files_route_returns_safe_metadata_without_file_bodies_or_model_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=False)
    rig = make_route_rig(tmp_path)
    router = BlockingModelRouter()
    rig.api.app.state.model_router_service = router
    active = file_list_workspace(tmp_path)

    response = rig.api.post(FILES_ROUTE, json={"workspace_path": str(active), "max_files": 20})
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["workspace_label"] == active.name
    assert payload["limits"]["max_files"] == 20
    assert str(active) not in json.dumps(payload)
    assert "OPENAI_API_KEY" not in json.dumps(payload)
    assert "sk-secret" not in json.dumps(payload)
    assert "raw generated log" not in json.dumps(payload)
    assert "content" not in json.dumps(payload).casefold()
    assert router.calls == 0
    assert rig.api.get(BASE).json() == {"runs": [], "count": 0}

    files = {item["path"]: item for item in payload["files"]}
    assert files["platformio.ini"]["selectable"] is True
    assert files["platformio.ini"]["kind"] == "config"
    assert files["src/main.cpp"]["selectable"] is True
    assert files[".env"]["selectable"] is False
    assert files[".env"]["reason"] == "secret_path"
    assert files[".pio/"]["selectable"] is False
    assert files[".pio/"]["reason"] == "generated_folder"
    assert ".pio/build.log" not in files
    assert all(".." not in item["path"].split("/") for item in payload["files"])


def test_context_files_route_rejects_invalid_workspace_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=False)
    rig = make_route_rig(tmp_path)

    response = rig.api.post(FILES_ROUTE, json={"workspace_path": str(tmp_path / "missing" / ".." / "missing")})

    assert response.status_code == 422
    assert response.json()["code"] == "CODING_WORKFLOW_WORKSPACE_INVALID"
