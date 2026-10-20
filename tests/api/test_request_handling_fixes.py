"""Message validation and storage through the actual local API."""
import json
import pytest
from backend.contracts.runtime_policy import MAX_AGENT_MESSAGE_CHARACTERS
from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env


def test_long_agent_instruction_is_accepted_and_preserved_without_editing_the_project(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=True)
    client, project_id, _ = make_client(tmp_path)
    with client as api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201
        session_id = created.json()["session"]["session_id"]
        path = f"/agent-runtime/sessions/{session_id}/messages"
        too_long = api.post(path, json={"content": "x" * (MAX_AGENT_MESSAGE_CHARACTERS + 1)})
        assert too_long.status_code == 422
        error = too_long.json()["details"]["errors"][0]
        assert error["location"] == ["body", "content"] and error["type"] == "string_too_long"
        assert api.get(f"/agent-runtime/sessions/{session_id}").json()["session"]["messages"] == []
        content = "Read the current project and report its contents. " + "x" * 25000
        response = api.post(path, json={"content": content, "provider_id": "fake_planner", "autonomy": "read_only"})
        assert response.status_code == 202, response.text
        assert response.json()["session"]["messages"][0]["content"] == content
        saved = api.get(f"/agent-runtime/sessions/{session_id}").json()["session"]["messages"]
        assert saved[0]["content"] == content


def test_oversize_chat_and_direct_run_requests_fail_before_starting_work(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=True)
    client, project_id, _ = make_client(tmp_path)
    with client as api:
        oversized = "x" * (MAX_AGENT_MESSAGE_CHARACTERS + 1)
        chat = api.post("/v1/chat/sessions", json={}).json()["session"]
        rejected = api.post(f"/v1/chat/sessions/{chat['session_id']}/messages", json={"content": oversized})
        assert rejected.status_code == 422
        run = api.post("/agent-runtime/runs", json={"project_id": project_id, "instruction": oversized})
        assert run.status_code == 422
        assert run.json()["details"]["errors"][0]["location"] == ["body", "instruction"]
        assert api.get(f"/v1/chat/sessions/{chat['session_id']}").json()["session"]["messages"] == []


@pytest.mark.parametrize("instruction", ["x" * MAX_AGENT_MESSAGE_CHARACTERS,
    "😀" * MAX_AGENT_MESSAGE_CHARACTERS], ids=["ascii", "escaped-unicode"])
def test_direct_run_body_limit_accepts_the_full_character_limit(tmp_path, monkeypatch, instruction):
    setup_env(monkeypatch, tmp_path, enabled=True)
    client, _, _ = make_client(tmp_path)
    with client as api:
        # Lookup happens after body and field validation; no task can start for this missing project.
        response = api.post("/agent-runtime/runs", content=json.dumps({"project_id": "missing-project",
            "instruction": instruction, "autonomy": "read_only"}), headers={"Content-Type": "application/json"})
        assert response.status_code == 404, response.text
