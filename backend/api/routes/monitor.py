"""Serial connection lifecycle routes."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ...services.serial_service import SerialConfiguration, SerialService
from ...services.serial_stream_broker import SerialStreamBroker
from ..dependencies import resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.monitor import MonitorStartRequest, MonitorStatusResponse

router = APIRouter(prefix="/monitor", tags=["monitor"])
logger = logging.getLogger(__name__)


@router.post(
    "/start",
    response_model=MonitorStatusResponse,
    responses=error_responses(409, 500, 503),
    summary="Start the serial monitor connection",
)
async def start_monitor(body: MonitorStartRequest, request: Request) -> MonitorStatusResponse:
    if body.project_id:
        metadata = await resolve_project_from_request(request, body.project_id)
        root = Path(metadata.project_path)
        ini = root / "platformio.ini"
        logger.info(
            "Running monitor in workspace root: %s project_id=%s platformio_ini=%s platformio_exists=%s",
            root,
            body.project_id,
            ini,
            ini.is_file(),
        )
    lock: asyncio.Lock = request.app.state.monitor_lock
    async with lock:
        current = getattr(request.app.state, "serial_service", None)
        current_state = getattr(getattr(current, "state", None), "name", "")
        if current is not None and current_state in {"CONNECTING", "CONNECTED", "RECONNECTING"}:
            configuration = current.configuration
            if (
                configuration.port == body.port
                and configuration.baudrate == body.baudrate
                and configuration.timeout_s == body.timeout_s
            ):
                await _stream_broker(request).attach(current)
                return _status(current)
            raise APIError(409, "MONITOR_ALREADY_RUNNING", "Serial monitor is already running")
        broker = _stream_broker(request)
        if current is not None:
            try:
                await current.disconnect()
            finally:
                await broker.detach()
        configuration = SerialConfiguration(
            port=body.port,
            baudrate=body.baudrate,
            timeout_s=body.timeout_s,
        )
        service = request.app.state.serial_service_factory(configuration)
        try:
            await service.connect()
        except Exception as exc:
            raise APIError(503, "SERIAL_CONNECTION_FAILED", "Serial connection could not be opened") from exc
        request.app.state.serial_service = service
        request.app.state.serial_runtime = service
        await broker.attach(service)
        return _status(service)


@router.post(
    "/stop",
    response_model=MonitorStatusResponse,
    responses=error_responses(500),
    summary="Stop the serial monitor connection",
)
async def stop_monitor(request: Request) -> MonitorStatusResponse:
    lock: asyncio.Lock = request.app.state.monitor_lock
    async with lock:
        service = getattr(request.app.state, "serial_service", None)
        if service is None:
            await _stream_broker(request).detach()
            return MonitorStatusResponse(
                state="DISCONNECTED",
                connected=False,
                port=None,
                baudrate=115_200,
                metrics={},
            )
        try:
            await service.disconnect()
        except Exception as exc:
            raise APIError(500, "SERIAL_DISCONNECT_FAILED", "Serial connection could not be closed") from exc
        await _stream_broker(request).detach()
        return _status(service)


@router.get("/stream", summary="Stream live serial observations")
async def monitor_stream(
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    broker = _stream_broker(request)

    async def stream():
        replay, queue = await broker.subscribe(after)
        try:
            for event in replay:
                yield _sse_event(event.to_dict())
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse_event(event.to_dict())
        finally:
            await broker.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/status",
    response_model=MonitorStatusResponse,
    summary="Get serial monitor status",
)
async def monitor_status(request: Request) -> MonitorStatusResponse:
    service = getattr(request.app.state, "serial_service", None)
    if service is None:
        return MonitorStatusResponse(
            state="DISCONNECTED",
            connected=False,
            port=None,
            baudrate=115_200,
            metrics={},
        )
    return _status(service)


def _status(service: SerialService) -> MonitorStatusResponse:
    state = service.state
    metrics: Any = service.metrics
    snapshot = getattr(metrics, "snapshot", None)
    if callable(snapshot):
        metrics = snapshot()
    if not isinstance(metrics, dict):
        metrics = {}
    return MonitorStatusResponse(
        state=getattr(state, "name", str(state)),
        connected=service.is_connected(),
        port=service.active_port,
        baudrate=service.configuration.baudrate,
        metrics=metrics,
    )


def _stream_broker(request: Request) -> SerialStreamBroker:
    broker = getattr(request.app.state, "serial_stream_broker", None)
    if not isinstance(broker, SerialStreamBroker):
        raise APIError(503, "SERIAL_STREAM_UNAVAILABLE", "Serial stream is unavailable")
    return broker


def _sse_event(payload: dict[str, object]) -> str:
    sequence = int(payload["sequence"])
    return f"id: {sequence}\nevent: observation\ndata: {json.dumps(payload, ensure_ascii=True)}\n\n"
