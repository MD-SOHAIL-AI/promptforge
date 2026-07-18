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
from backend.operations.resilience import sanitize

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
            "message": sanitize(record.getMessage(), "message"),
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
            payload["exception_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Exception"
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
    execution_id = _correlation(request.headers.get("X-Execution-ID"), "execution")
    workflow_id = _correlation(request.headers.get("X-Workflow-Correlation-ID"), "workflow")
    execution_token = execution_id_context.set(execution_id)
    workflow_token = workflow_id_context.set(workflow_id)
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
        workflow_id_context.reset(workflow_token)
        execution_id_context.reset(execution_token)
        request_id_context.reset(token)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "execution_id": response.headers.get(
                "X-Execution-ID",
                execution_id,
            ),
            "workflow_correlation_id": response.headers.get(
                "X-Workflow-Correlation-ID",
                workflow_id,
            ),
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


def new_correlation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _request_id(candidate: str | None) -> str:
    if candidate and len(candidate) <= 128 and all(
        character.isalnum() or character in "._-" for character in candidate
    ):
        return candidate
    return new_correlation_id("request")


def _correlation(candidate: str | None, prefix: str) -> str:
    if candidate and len(candidate) <= 128 and all(character.isalnum() or character in "._-" for character in candidate):
        return candidate
    return new_correlation_id(prefix)
