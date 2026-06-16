"""Serial connection lifecycle routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request

from ...services.serial_service import SerialConfiguration, SerialService
from ..errors import APIError, error_responses
from ..schemas.monitor import MonitorStartRequest, MonitorStatusResponse

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.post(
    "/start",
    response_model=MonitorStatusResponse,
    responses=error_responses(409, 500, 503),
    summary="Start the serial monitor connection",
)
async def start_monitor(body: MonitorStartRequest, request: Request) -> MonitorStatusResponse:
    lock: asyncio.Lock = request.app.state.monitor_lock
    async with lock:
        current = getattr(request.app.state, "serial_service", None)
        if current is not None and current.is_connected():
            configuration = current.configuration
            if (
                configuration.port == body.port
                and configuration.baudrate == body.baudrate
                and configuration.timeout_s == body.timeout_s
            ):
                return _status(current)
            raise APIError(409, "MONITOR_ALREADY_RUNNING", "Serial monitor is already running")
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
        return _status(service)


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
