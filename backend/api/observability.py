"""Request correlation and structured API logging."""

from __future__ import annotations

import contextvars
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..observability.diagnostics import emit_diagnostic_event, redact

request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id",
    default="",
)
execution_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "execution_id",
    default="",
)
workflow_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "workflow_correlation_id",
    default="",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "request_id",
            "execution_id",
            "workflow_correlation_id",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "task_id",
            "event",
            "exception_type",
        ):
            value = getattr(record, key, None)
            if value not in (None, ""):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_api_logging() -> logging.Logger:
    logger = logging.getLogger("promptforge.api")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


async def request_context_middleware(request: Request, call_next: Any) -> Any:
    logger = logging.getLogger("promptforge.api")
    request_id = _request_id(request.headers.get("X-Request-ID"))
    token = request_id_context.set(request_id)
    started = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        logger.exception(
            "Unhandled API exception",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "exception_type": type(exc).__name__,
            },
        )
        response = JSONResponse(
            status_code=500,
            content={
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
                "details": {},
            },
        )
    finally:
        duration_ms = round((time.monotonic() - started) * 1000)
        request_id_context.reset(token)
    response.headers["X-Request-ID"] = request_id
    _emit_http_diagnostic(
        request=request,
        request_id=request_id,
        response=response,
        duration_ms=duration_ms,
    )
    log_method = logger.debug if _is_polling_request(request.method, request.url.path) and response.status_code < 400 else logger.info
    log_method(
        "request_completed",
        extra={
            "request_id": request_id,
            "execution_id": response.headers.get(
                "X-Execution-ID",
                execution_id_context.get(),
            ),
            "workflow_correlation_id": response.headers.get(
                "X-Workflow-Correlation-ID",
                workflow_id_context.get(),
            ),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


def _emit_http_diagnostic(*, request: Request, request_id: str, response: Any, duration_ms: int) -> None:
    path = request.url.path
    status_code = int(getattr(response, "status_code", 0) or 0)
    polling = _is_polling_request(request.method, path)
    if polling and status_code < 400:
        level = "DEBUG"
    else:
        level = "ERROR" if status_code >= 500 else "WARNING" if status_code >= 400 else "INFO"
    if level == "DEBUG":
        return
    emit_diagnostic_event(
        component="backend",
        event="http.request.completed",
        level=level,
        request_id=request_id,
        method=request.method,
        path=path,
        status=status_code,
        status_code=status_code,
        duration_ms=duration_ms,
        session_id=request.path_params.get("session_id"),
        run_id=request.path_params.get("run_id"),
        project_id=request.query_params.get("project_id"),
        **(_rejected_client(request) if status_code in {401, 403} else {}),
    )


def _rejected_client(request: Request) -> dict[str, Any]:
    """Identify rejected local callers without recording credentials or bodies."""
    client = request.client
    return {"client_host": client.host if client else None,
            "client_port": client.port if client else None,
            "client_user_agent": redact(request.headers.get("user-agent", "")[:256])}


def _is_polling_request(method: str, path: str) -> bool:
    if method.upper() != "GET":
        return False
    if path in {"/health", "/projects", "/build-history", "/logs", "/monitor/status", "/devices/boards", "/models/providers", "/models/routes", "/agent-runtime/providers"}:
        return True
    return (
        path.startswith("/agent-runtime/runs/")
        and (path.endswith("/events") or path.endswith("/graph"))
    ) or (
        path.startswith("/agent-runtime/sessions/")
        and (path.endswith("/events") or path.endswith("/activity"))
    )


def new_correlation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _request_id(candidate: str | None) -> str:
    if candidate and len(candidate) <= 128 and all(
        character.isalnum() or character in "._-" for character in candidate
    ):
        return candidate
    return new_correlation_id("request")
