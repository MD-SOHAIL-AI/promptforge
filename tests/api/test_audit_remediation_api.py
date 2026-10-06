"""HTTP regressions for staged legacy edits and chunked request limits."""
import asyncio
from pathlib import Path

import httpx

from tests.api.test_api_routes import make_client, completed_outcome


def test_legacy_execute_stages_edits_and_publishes_a_reviewable_changeset(tmp_path):
    async def execute(prompt, **kwargs):
        root = Path(kwargs["active_workspace"]["rootPath"])
        (root / "note.txt").write_text("after", encoding="utf-8")
        await kwargs["progress_callback"]("WORKFLOW_COMPLETED", {"status": "COMPLETED"})
        return completed_outcome(kwargs["task_id"])
    client, project_id, _ = make_client(tmp_path, execute_prompt_fn=execute)
    with client:
        owner = client.app.state.services
        original = owner.change_set_service.create_from_stage
        published = []
        def create(**kwargs):
            value = original(**kwargs)
            published.append(value)
            return value
        owner.change_set_service.create_from_stage = create
        response = client.post("/execute", json={"prompt": "Update notes", "project_id": project_id})
        assert response.status_code == 200
        # Wait on the actual task through the portal; no fixed sleep assumption.
        from backend.api.routes.execute import _active_executions
        active = _active_executions.get(response.json()["task_id"])
        if active is not None:
            client.portal.call(lambda: active.task)
        assert len(published) == 1
        change = published[0]
        assert change.status == "pending" and change.review_required
        root = Path(change.workspace_root)
        assert not (root / "note.txt").exists()
        assert (Path(change.staging_root) / "note.txt").read_text() == "after"
        owner.change_set_service.apply(change.change_set_id)
        assert (root / "note.txt").read_text() == "after"


def test_agent_body_limit_checks_chunked_bytes_without_content_length(tmp_path):
    client, project_id, _ = make_client(tmp_path)
    async def scenario():
        async def chunks():
            for _ in range(9):
                yield b"x" * 4096
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=client.app),
                base_url="http://127.0.0.1:8000", headers=dict(client.headers)) as http:
            response = await http.post("/agent-runtime/runs", content=chunks())
            assert "content-length" not in response.request.headers
            assert response.status_code == 413
            assert response.json()["code"] == "REQUEST_TOO_LARGE"
    with client:
        asyncio.run(scenario())
