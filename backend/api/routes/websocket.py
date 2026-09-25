"""Execution progress WebSocket route."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..progress import ExecutionProgressHub
from ..local_security import websocket_is_authorized
from ..schemas.websocket import ExecutionEventType
from ...observability.diagnostics import emit_diagnostic_event

router = APIRouter(tags=["execution"])


@router.websocket("/ws/execution/{task_id}")
async def execution_progress(websocket: WebSocket, task_id: str) -> None:
    authorized, reason = websocket_is_authorized(websocket)
    if not authorized:
        await websocket.close(code=1008, reason=reason)
        return
    if not _valid_task_id(task_id):
        await websocket.close(code=1008, reason="Invalid task ID")
        return
    try:
        after = int(websocket.query_params.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError:
        await websocket.close(code=1008, reason="Invalid replay sequence")
        return

    hub: ExecutionProgressHub = websocket.app.state.services.progress_hub
    queue, replay = await hub.subscribe(task_id, after=after)
    await websocket.accept()
    emit_diagnostic_event(component="backend", event="websocket.connected", task_id=task_id, after_sequence=after)

    async def sender() -> None:
        for event in replay:
            await websocket.send_json(event.model_dump(mode="json"))
            if event.event in {
                ExecutionEventType.WORKFLOW_COMPLETED,
                ExecutionEventType.WORKFLOW_FAILED,
                ExecutionEventType.WORKFLOW_CANCELLED,
            }:
                await websocket.close(code=1000)
                return
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
            if event.event in {
                ExecutionEventType.WORKFLOW_COMPLETED,
                ExecutionEventType.WORKFLOW_FAILED,
                ExecutionEventType.WORKFLOW_CANCELLED,
            }:
                await websocket.close(code=1000)
                return

    async def receiver() -> None:
        while websocket.client_state is WebSocketState.CONNECTED:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except RuntimeError:
                if websocket.client_state is not WebSocketState.DISCONNECTED:
                    raise
    except asyncio.CancelledError:
        # Client/test teardown cancels the ASGI task after disconnect; this is
        # normal lifecycle completion, not an application error.
        pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        emit_diagnostic_event(component="backend", event="websocket.error", level="ERROR", task_id=task_id, error_code=type(exc).__name__, message=str(exc) or type(exc).__name__)
        raise
    finally:
        sender_task.cancel()
        receiver_task.cancel()
        await asyncio.gather(
            sender_task,
            receiver_task,
            return_exceptions=True,
        )
        await hub.unsubscribe(task_id, queue)
        emit_diagnostic_event(component="backend", event="websocket.disconnected", task_id=task_id)


def _valid_task_id(value: str) -> bool:
    return (
        value.startswith("task-")
        and len(value) <= 128
        and all(character.isalnum() or character in "._-" for character in value)
    )
