"""Local diagnostics ingestion for renderer/runtime events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...observability.diagnostics import emit_diagnostic_event
from ..local_security import require_local_http_request

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


class DiagnosticEventRequest(BaseModel):
    component: str = Field(min_length=1, max_length=128)
    event: str = Field(min_length=1, max_length=160)
    level: str = Field(default="INFO", min_length=1, max_length=16)
    message: str | None = Field(default=None, max_length=4000)
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/events", status_code=202)
async def ingest_diagnostic_event(body: DiagnosticEventRequest, request: Request) -> dict[str, object]:
    require_local_http_request(request, purpose="Runtime diagnostics")
    emit_diagnostic_event(
        component=body.component,
        event=body.event,
        level=body.level,
        message=body.message,
        **body.fields,
    )
    return {"accepted": True}
