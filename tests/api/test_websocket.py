from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

from backend.api.progress import ExecutionProgressHub
from backend.services.terminal_service import TerminalService, TerminalSession

from .test_api_routes import make_client


class DummyProcess:
    returncode = None
    stdin = None
    stdout = None
    stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode or 0)


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


def test_terminal_websocket_replays_output_and_resizes(tmp_path: Path) -> None:
    service = TerminalService()
    session = TerminalSession(
        session_id="term-ws-test",
        shell="PowerShell",
        cwd=str(tmp_path),
        process=DummyProcess(),  # type: ignore[arg-type]
        events=deque(maxlen=20),
    )
    service._sessions[session.session_id] = session  # noqa: SLF001
    service._append(session, "stdout", "ready\r\n")  # noqa: SLF001
    client, _, _ = make_client(tmp_path, terminal_service=service)
    with client:
        with client.websocket_connect("/terminal/sessions/term-ws-test/stream") as websocket:
            output = websocket.receive_json()
            assert output["type"] == "output"
            assert output["event"]["data"] == "ready\r\n"

            websocket.send_json({"type": "resize", "cols": 120, "rows": 36})
            resized = websocket.receive_json()

    assert resized["type"] == "resized"
    assert resized["session"]["cols"] == 120
    assert resized["session"]["rows"] == 36


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
