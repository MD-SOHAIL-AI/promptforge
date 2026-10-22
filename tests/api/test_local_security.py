from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.app import create_app
from backend.api.local_security import LOCAL_SESSION_COOKIE

from .test_api_routes import make_client


def _host_headers(**extra: str) -> dict[str, str]:
    return {"Host": "127.0.0.1:8000", **extra}


def test_health_remains_available_for_desktop_port_probe(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", "t" * 43)
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["local_auth_required"] is True
    assert len(response.json()["local_auth_fingerprint"]) == 24


def test_desktop_api_rejects_missing_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", "t" * 43)
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get("/settings", headers=_host_headers())
    assert response.status_code == 401
    assert response.json()["code"] == "LOCAL_AUTH_REQUIRED"


def test_desktop_api_accepts_bearer_and_http_only_cookie_equivalent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "s" * 43
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", token)
    client, _, _ = make_client(tmp_path)
    with client:
        bearer = client.get("/settings", headers=_host_headers(Authorization=f"Bearer {token}"))
        cookie = client.get("/settings", headers=_host_headers(Cookie=f"{LOCAL_SESSION_COOKIE}={token}"))
    assert bearer.status_code == 200
    assert cookie.status_code == 200


def test_desktop_api_rejects_non_loopback_host_even_with_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "h" * 43
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", token)
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get(
            "/settings",
            headers={"Host": "evil.example", "Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "LOCAL_HOST_REJECTED"


def test_desktop_api_rejects_remote_origin_even_with_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "o" * 43
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", token)
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get(
            "/settings",
            headers=_host_headers(Authorization=f"Bearer {token}", Origin="https://remote.example"),
        )
    assert response.status_code == 403
    assert response.json()["code"] == "REMOTE_ORIGIN_REJECTED"


def test_terminal_route_is_blocked_before_shell_spawn_without_token(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", "x" * 43)
    client, project_id, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/terminal/sessions",
            headers=_host_headers(),
            json={"project_id": project_id},
        )
    assert response.status_code == 401
    assert response.json()["code"] == "LOCAL_AUTH_REQUIRED"


def test_execution_websocket_requires_authenticated_local_session(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "w" * 43
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", token)
    client, _, _ = make_client(tmp_path)
    with client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                "/ws/execution/task-auth-denied",
                headers=_host_headers(),
            ):
                pass
        assert denied.value.code == 1008

        with client.websocket_connect(
            "/ws/execution/task-auth-ok",
            headers=_host_headers(Cookie=f"{LOCAL_SESSION_COOKIE}={token}"),
        ):
            pass


def test_execution_websocket_rejects_remote_origin(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    token = "r" * 43
    monkeypatch.setenv("FORGEX_LOCAL_TOKEN", token)
    client, _, _ = make_client(tmp_path)
    with client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(
                "/ws/execution/task-auth-origin",
                headers=_host_headers(Cookie=f"{LOCAL_SESSION_COOKIE}={token}", Origin="https://remote.example"),
            ):
                pass
        assert denied.value.code == 1008


def test_desktop_mode_refuses_to_start_without_local_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORGEX_DESKTOP", "1")
    monkeypatch.delenv("FORGEX_LOCAL_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="FORGEX_LOCAL_TOKEN"):
        create_app()


def test_non_desktop_without_token_fails_closed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FORGEX_LOCAL_TOKEN", raising=False)
    monkeypatch.delenv("FORGEX_DESKTOP", raising=False)
    app = create_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health", headers={"Host": "evil.example:8000"}).json()["code"] == "LOCAL_HOST_REJECTED"
        denied = client.post("/execute", json={"prompt": "Build"})
        assert denied.status_code == 503
        assert denied.json()["code"] == "LOCAL_TOKEN_REQUIRED"
        assert client.get("/settings", headers={"Host": "evil.example:8000"}).json()["code"] == "LOCAL_HOST_REJECTED"
        assert client.get("/settings", headers={"Origin": "null"}).json()["code"] == "REMOTE_ORIGIN_REJECTED"
        with pytest.raises(WebSocketDisconnect) as websocket_denied:
            with client.websocket_connect("/ws/execution/task-no-token"):
                pass
        assert websocket_denied.value.code == 1008
