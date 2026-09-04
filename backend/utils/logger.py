"""Structured JSON logging for PromptForge runtime components."""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

__all__ = ["PromptForgeLogger"]


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        category = getattr(record, "category", None)
        if category is not None:
            payload["category"] = category
        context = getattr(record, "structured_context", None)
        if context:
            payload["context"] = context
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )


class PromptForgeLogger:
    """Small structured logger that emits one JSON object per line."""

    __slots__ = ("_logger",)

    def __init__(
        self,
        name: str = "promptforge",
        *,
        level: int | str = logging.INFO,
        stream: TextIO | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip() or "\x00" in name:
            raise ValueError("name must be a non-empty string")
        resolved_level = _resolve_level(level)
        logger = logging.Logger(name.strip(), resolved_level)
        logger.propagate = False
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setLevel(resolved_level)
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)
        self._logger = logger

    def info(self, message: str, **context: Any) -> None:
        """Log an informational structured event."""

        self._log(logging.INFO, message, context=context)

    def warning(self, message: str, **context: Any) -> None:
        """Log a warning structured event."""

        self._log(logging.WARNING, message, context=context)

    def error(self, message: str, **context: Any) -> None:
        """Log an error structured event."""

        self._log(logging.ERROR, message, context=context)

    def debug(self, message: str, **context: Any) -> None:
        """Log a debug structured event."""

        self._log(logging.DEBUG, message, context=context)

    def workflow(self, message: str, **context: Any) -> None:
        """Log an informational workflow event."""

        self._log(
            logging.INFO,
            message,
            category="workflow",
            context=context,
        )

    def build(self, message: str, **context: Any) -> None:
        """Log an informational firmware build event."""

        self._log(
            logging.INFO,
            message,
            category="build",
            context=context,
        )

    def flash(self, message: str, **context: Any) -> None:
        """Log an informational firmware flash event."""

        self._log(
            logging.INFO,
            message,
            category="flash",
            context=context,
        )

    def monitor(self, message: str, **context: Any) -> None:
        """Log an informational serial monitor event."""

        self._log(
            logging.INFO,
            message,
            category="monitor",
            context=context,
        )

    def log_workflow(self, message: str, **context: Any) -> None:
        self.workflow(message, **context)

    def log_build(self, message: str, **context: Any) -> None:
        self.build(message, **context)

    def log_flash(self, message: str, **context: Any) -> None:
        self.flash(message, **context)

    def log_monitor(self, message: str, **context: Any) -> None:
        self.monitor(message, **context)

    def _log(
        self,
        level: int,
        message: str,
        *,
        context: Mapping[str, Any],
        category: str | None = None,
    ) -> None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        extra = {
            "category": category,
            "structured_context": dict(context),
        }
        self._logger.log(level, message, extra=extra)


def _resolve_level(value: int | str) -> int:
    if isinstance(value, bool):
        raise ValueError("level must be a logging level")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("level must be non-negative")
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        resolved = logging.getLevelName(normalized)
        if isinstance(resolved, int):
            return resolved
    raise ValueError("level must be a valid logging level")


def _json_default(value: object) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )
    if isinstance(value, (Path, Enum)):
        return value.value if isinstance(value, Enum) else str(value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return repr(value)
