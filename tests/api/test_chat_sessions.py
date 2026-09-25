import time

from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env
from tests.unit.test_chat_progress import Router


def test_chat_uses_existing_session_routes_without_agent_execution(
    tmp_path, monkeypatch
):
    setup_env(monkeypatch, tmp_path, enabled=False)
    api, _, _ = make_client(tmp_path)
    api.app.state.services.chat_service.router = Router()
    with api:
        created = api.post("/agent-runtime/sessions", json={"kind": "chat"})
        assert created.status_code == 201, created.text
        session = created.json()["session"]
        identity = session["session_id"]
        assert session["project_id"] is None
        sent = api.post(
            f"/agent-runtime/sessions/{identity}/messages",
            json={"content": "Explain a thermistor."},
        )
        assert sent.status_code == 202, sent.text
        assert sent.json()["run"] is None
        for _ in range(100):
            restored = api.get(f"/agent-runtime/sessions/{identity}").json()["session"]
            if restored["messages"][-1]["metadata"]["status"] == "completed":
                break
            time.sleep(0.01)
        assert restored["messages"][-1]["content"] == "Hello **world**"
        assert len(api.get("/agent-runtime/sessions?kind=chat").json()["sessions"]) == 1
        assert api.get("/agent-runtime/sessions").json()["sessions"] == []
        assert (
            api.patch(
                f"/agent-runtime/sessions/{identity}",
                json={"title": "Thermistor notes"},
            ).json()["session"]["title"]
            == "Thermistor notes"
        )
        assert api.delete(f"/agent-runtime/sessions/{identity}").status_code == 200
        assert api.get(f"/agent-runtime/sessions/{identity}").status_code == 404


def test_chat_attachment_cannot_escape_project(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=False)
    api, project, _ = make_client(tmp_path)
    with api:
        identity = api.post("/agent-runtime/sessions", json={"kind": "chat"}).json()[
            "session"
        ]["session_id"]
        response = api.post(
            f"/agent-runtime/sessions/{identity}/messages",
            json={
                "content": "Read this",
                "context_project_id": project,
                "context_paths": ["../secret.txt"],
            },
        )
        assert response.status_code == 422
        assert (
            api.get(f"/agent-runtime/sessions/{identity}").json()["session"]["messages"]
            == []
        )
