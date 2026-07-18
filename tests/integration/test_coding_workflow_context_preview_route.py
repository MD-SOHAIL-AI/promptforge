from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.test_coding_workflow_routes import BASE, configure_env, make_route_rig, make_workspace


PREVIEW_ROUTE = f"{BASE}/context/preview"


class BlockingModelRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def generate_model(self, request: object) -> object:
        self.calls += 1
        raise AssertionError("context preview must not call model_router")


def preview_workspace(tmp_path: Path) -> Path:
    active = make_workspace(tmp_path)
    (active / "platformio.ini").write_text("[env:esp32dev]\nframework = arduino\n", encoding="utf-8")
    (active / "src").mkdir(exist_ok=True)
    (active / "src" / "main.cpp").write_text("#include <Arduino.h>\nvoid setup() {}\n", encoding="utf-8")
    (active / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    (active / "node_modules").mkdir()
    (active / "node_modules" / "pkg.js").write_text("generated\n", encoding="utf-8")
    return active


def test_context_preview_disabled_when_unified_flag_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=False, fake=False)
    rig = make_route_rig(tmp_path)
    active = preview_workspace(tmp_path)

    response = rig.api.post(PREVIEW_ROUTE, json={"workspace_path": str(active), "context_mode": "selected_files"})

    assert response.status_code == 403
    assert response.json()["code"] == "UNIFIED_CODING_WORKFLOW_DISABLED"


def test_selected_files_preview_returns_safe_metadata_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=False)
    rig = make_route_rig(tmp_path)
    blocking_router = BlockingModelRouter()
    rig.api.app.state.model_router_service = blocking_router
    active = preview_workspace(tmp_path)

    response = rig.api.post(
        PREVIEW_ROUTE,
        json={
            "workspace_path": str(active),
            "prompt": "repair blink",
            "context_mode": "selected_files",
            "selected_files": ["platformio.ini", "src/main.cpp", ".env", "../evil.txt"],
        },
    )
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["context_mode"] == "selected_files"
    assert payload["workspace_label"] == active.name
    assert [(item["path"], item["kind"], item["truncated"]) for item in payload["included_files"]] == [
        ("platformio.ini", "config", False),
        ("src/main.cpp", "cpp", False),
    ]
    assert all(item["size_bytes"] > 0 for item in payload["included_files"])
    excluded = {item["path"]: item["reason"] for item in payload["excluded_files"]}
    assert excluded[".env"] == "sensitive_path"
    assert excluded["../evil.txt"] == "path_traversal"
    assert "content" not in json.dumps(payload).casefold()
    assert "OPENAI_API_KEY" not in json.dumps(payload)
    assert "sk-secret" not in json.dumps(payload)
    assert str(active) not in json.dumps(payload)
    assert blocking_router.calls == 0
    assert rig.api.get(BASE).json() == {"runs": [], "count": 0}


def test_project_summary_preview_includes_bounded_tree_and_excludes_generated_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=False)
    rig = make_route_rig(tmp_path)
    active = preview_workspace(tmp_path)

    response = rig.api.post(
        PREVIEW_ROUTE,
        json={
            "workspace_path": str(active),
            "context_mode": "project_summary",
            "max_files": 5,
            "max_file_bytes": 32768,
            "max_total_bytes": 131072,
        },
    )
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["context_mode"] == "project_summary"
    assert "platformio.ini" in [item["path"] for item in payload["included_files"]]
    assert "README.md" in [item["path"] for item in payload["included_files"]]
    assert "src/main.cpp" in payload["tree_summary"]
    excluded = {item["path"]: item["reason"] for item in payload["excluded_files"]}
    assert excluded[".env"] == "sensitive_path"
    assert excluded["node_modules"] == "generated_or_ignored_path"
    assert all(not item.startswith(str(active)) for item in payload["tree_summary"])
    assert str(active) not in json.dumps(payload)


def test_context_preview_respects_size_limits_and_does_not_create_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=False)
    rig = make_route_rig(tmp_path)
    active = preview_workspace(tmp_path)

    response = rig.api.post(
        PREVIEW_ROUTE,
        json={
            "workspace_path": str(active),
            "context_mode": "selected_files",
            "selected_files": ["platformio.ini", "src/main.cpp"],
            "max_files": 1,
        },
    )
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["truncated"] is True
    assert [item["path"] for item in payload["included_files"]] == ["platformio.ini"]
    assert payload["excluded_files"][-1] == {"path": "src/main.cpp", "reason": "max_files_exceeded"}
    assert rig.api.get(BASE).json() == {"runs": [], "count": 0}
