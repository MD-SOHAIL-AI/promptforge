"""Structured API error handling."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .schemas.common import ErrorResponse

logger = logging.getLogger("promptforge.api")


class APIError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_responses(*status_codes: int) -> dict[int, dict[str, object]]:
    return {
        status_code: {"model": ErrorResponse}
        for status_code in status_codes
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        del request
        return _response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del request
        details = {
            "errors": [
                {
                    "location": [str(item) for item in error["loc"]],
                    "message": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors()
            ]
        }
        return _response(422, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return _response(exc.status_code, "HTTP_ERROR", message, {})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled API exception",
            extra={"path": request.url.path, "exception_type": type(exc).__name__},
        )
        return _response(500, "INTERNAL_ERROR", "An internal error occurred", {})


def _response(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any],
) -> JSONResponse:
    payload = ErrorResponse(code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
