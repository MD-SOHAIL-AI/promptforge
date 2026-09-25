import time

from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env
from tests.unit.test_chat_progress import Router


def test_chat_routes_work_without_agent_and_reject_agent_controls(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=False)
    api, project, _ = make_client(tmp_path)
    api.app.state.services.chat_service.router = Router()
    with api:
        created = api.post("/v1/chat/sessions", json={})
        assert created.status_code == 201
        identity = created.json()["session"]["session_id"]
        path = f"/v1/chat/sessions/{identity}"
        for field in ("autonomy", "board_port", "approved_plan_id"):
            assert api.post(path + "/messages", json={"content": "Flash it", field: "auto"}).status_code == 422
        assert api.post(path + "/messages", json={"content": "Build and flash my firmware"}).status_code == 202
        for _ in range(100):
            session = api.get(path).json()["session"]
            if session["messages"][-1]["metadata"]["status"] == "completed":
                break
            time.sleep(0.01)
        assert session["messages"][-1]["content"] == "Hello **world**"
        assert session["active_run_id"] is None
        agent = api.post("/agent-runtime/sessions", json={"project_id": project}).json()["session"]
        agent_path = f'/v1/chat/sessions/{agent["session_id"]}'
        assert api.get(agent_path).status_code == 404
        assert api.post(agent_path + "/cancel", json={}).status_code == 404
        assert api.delete(agent_path).status_code == 404
        assert api.patch(path, json={"title": "Notes"}).json()["session"]["title"] == "Notes"
        assert api.delete(path).status_code == 200
        assert api.get(path).status_code == 404
