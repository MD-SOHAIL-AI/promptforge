from __future__ import annotations

import asyncio
from pathlib import Path

from backend.api.progress import ExecutionProgressHub

from .test_api_routes import make_client


def test_websocket_live_events_and_terminal_close(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        with client.websocket_connect("/ws/execution/task-ws-test") as websocket:
            response = client.post(
                "/execute",
                json={"prompt": "Blink", "task_id": "task-ws-test"},
            )
            assert response.status_code == 200
            events = [websocket.receive_json() for _ in range(3)]
    assert [item["event"] for item in events] == [
        "TASK_CREATED",
        "PLAN_GENERATED",
        "WORKFLOW_COMPLETED",
    ]
    assert [item["sequence"] for item in events] == [1, 2, 3]
    assert len({item["execution_id"] for item in events}) == 1


def test_websocket_replay_and_invalid_task_id(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/execute",
            json={"prompt": "Blink", "task_id": "task-replay-test"},
        )
        assert response.status_code == 200
        with client.websocket_connect(
            "/ws/execution/task-replay-test?after=1"
        ) as websocket:
            assert websocket.receive_json()["event"] == "PLAN_GENERATED"
            assert websocket.receive_json()["event"] == "WORKFLOW_COMPLETED"

        try:
            with client.websocket_connect("/ws/execution/invalid/id"):
                raise AssertionError("invalid WebSocket path unexpectedly connected")
        except Exception:
            pass


def test_websocket_client_disconnect_is_clean(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        with client.websocket_connect("/ws/execution/task-disconnect-test"):
            pass

        response = client.get("/health")

    assert response.status_code == 200


def test_duplicate_task_id_returns_conflict(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        first = client.post(
            "/execute",
            json={"prompt": "Blink", "task_id": "task-duplicate"},
        )
        second = client.post(
            "/execute",
            json={"prompt": "Blink", "task_id": "task-duplicate"},
        )
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "TASK_ID_CONFLICT"


def test_progress_hub_accepts_failed_step_events() -> None:
    async def run() -> str:
        hub = ExecutionProgressHub()
        assert await hub.begin("task-failed-event")
        event = await hub.emit(
            event="CODE_GENERATION_FAILED",
            task_id="task-failed-event",
            execution_id="execution-test",
            workflow_correlation_id="workflow-test",
            payload={"success": False},
        )
        return event.event.value

    assert asyncio.run(run()) == "CODE_GENERATION_FAILED"


def test_progress_hub_accepts_cancelled_terminal_event() -> None:
    async def run() -> str:
        hub = ExecutionProgressHub()
        assert await hub.begin("task-cancelled-event")
        event = await hub.emit(
            event="WORKFLOW_CANCELLED",
            task_id="task-cancelled-event",
            execution_id="execution-test",
            workflow_correlation_id="workflow-test",
            payload={"status": "CANCELLED"},
        )
        return event.event.value

    assert asyncio.run(run()) == "WORKFLOW_CANCELLED"
