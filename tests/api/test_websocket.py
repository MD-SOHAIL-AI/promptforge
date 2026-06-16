from __future__ import annotations

from pathlib import Path

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
