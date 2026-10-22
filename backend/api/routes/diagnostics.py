"""Local diagnostics ingestion for renderer/runtime events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from ...observability.diagnostics import emit_diagnostic_event, redact, BACKEND_INSTANCE_ID
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
        **{key: value for key, value in body.fields.items() if key not in {"component", "event", "level", "message"}},
    )
    return {"accepted": True}


@router.get("/runs/{run_id}/export")
async def export_run_diagnostics(run_id: str, request: Request) -> dict[str, object]:
    require_local_http_request(request, purpose="Diagnostic export")
    from .agent_runtime import _service
    from ..errors import APIError
    from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT
    service = _service(request)
    try:
        run = service.get_run(run_id).to_safe_dict()
        events = service.events(run_id)
    except KeyError as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Run was not found.") from exc
    keys = {"event_type", "sequence", "run_id", "status", "classification", "tool", "call_id", "duration_ms", "build_id", "stage", "timestamp", "exit_code"}
    # Deliberately omit prompt/source/serial/log content and credentials.
    build = run.get("build_result") or {}
    from ...observability.log_reader import read_run_logs
    import asyncio
    logs = await asyncio.to_thread(read_run_logs, run_id)
    return redact({"schema_version": 1, "backend_instance_id": BACKEND_INSTANCE_ID,
        "source_fingerprint": BACKEND_SOURCE_FINGERPRINT,
        "app_version": request.app.version, "run": {key: run.get(key) for key in (
            "run_id", "project_id", "provider_id", "model_id", "status", "classification", "failure", "task_revision", "tool_execution_count", "repair_attempt_count", "recovery_actions")},
        "build": {key: build.get(key) for key in ("build_id", "success", "environment", "board", "toolchain", "exit_code", "firmware_hash", "workspace_revision", "error_code")},
        "events": [{key: event[key] for key in keys if key in event} for event in events[-2000:]],
        "runtime_logs": logs})


@router.get("/runtime")
async def diagnostic_runtime(request: Request) -> dict[str, object]:
    require_local_http_request(request, purpose="Runtime provenance")
    import hashlib
    import os
    from pathlib import Path
    from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT
    from backend.runtime_contract import BACKEND_RUNTIME_CONTRACT
    from ..dependencies import services
    root = Path(os.getenv("FORGEX_ROOT") or Path(__file__).resolve().parents[3])
    manifest = root / "release-manifest.json"
    owner = services(request)
    from datetime import datetime, timezone
    orchestrator = owner.agent_orchestrator
    return {"schema_version": 1, "backend_instance_id": BACKEND_INSTANCE_ID,
            "runtime_contract": BACKEND_RUNTIME_CONTRACT,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "data_profile": str(orchestrator.store.path.parent) if orchestrator else None,
            "source_fingerprint": BACKEND_SOURCE_FINGERPRINT,
            "packaged": os.getenv("FORGEX_DESKTOP") == "1" and manifest.is_file(),
            "resource_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
            "platformio": (await owner.platformio_service.health_status()).to_dict()}
