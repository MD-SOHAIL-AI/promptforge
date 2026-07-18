from __future__ import annotations

from pathlib import Path

import pytest

from tests.api.test_antigravity_bridge_routes import make_client


ENDPOINT = "/models/api-coding-agent/fake/generate-review"


def configure_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, enabled: bool) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    if enabled:
        monkeypatch.setenv("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "1")
    else:
        monkeypatch.setenv("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "0")


def test_fake_coding_agent_route_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure_root(monkeypatch, tmp_path, enabled=False)
    active = tmp_path / "active"
    active.mkdir()

    with make_client(tmp_path, enabled=False) as api:
        response = api.post(ENDPOINT, json={"prompt": "blink", "workspace_path": str(active)})

    assert response.status_code == 403
    assert response.json()["code"] == "FAKE_API_CODING_AGENT_DISABLED"


def test_fake_coding_agent_route_creates_review_in_managed_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_root(monkeypatch, tmp_path, enabled=True)
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("unchanged\n", encoding="utf-8")

    with make_client(tmp_path, enabled=False) as api:
        response = api.post(
            ENDPOINT,
            json={"prompt": "create platformio blink project", "workspace_path": str(active)},
        )
        payload = response.json()
        review_response = api.get(f"/models/bridges/reviews/{payload['review_id']}")

    assert response.status_code == 200
    assert payload["status"] == "review_created"
    assert payload["files"] == ["platformio.ini", "src/main.cpp"]
    assert payload["commands_suggested"] == [{"command": "pio run", "reason": "Build the PlatformIO project"}]
    assert review_response.status_code == 200
    assert review_response.json()["review"]["provider_id"] == "fake_api_coding_agent"
    assert "api-coding-agent-sandboxes" in Path(payload["review_path"]).parts
    assert (active / "README.md").read_text(encoding="utf-8") == "unchanged\n"
    assert not (active / "platformio.ini").exists()


@pytest.mark.parametrize("prompt", ["unsafe path", "protected path", "invalid json", "oversized"])
def test_fake_coding_agent_route_returns_structured_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
) -> None:
    configure_root(monkeypatch, tmp_path, enabled=True)
    active = tmp_path / "active"
    active.mkdir()

    with make_client(tmp_path, enabled=False) as api:
        response = api.post(ENDPOINT, json={"prompt": prompt, "workspace_path": str(active)})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["validation_errors"]
    assert list(active.iterdir()) == []
