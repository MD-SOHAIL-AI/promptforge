"""Workspace build history and log routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request

from ...workspace.workspace_manager import WorkspaceError, WorkspaceManager
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.workspace import (
    BuildHistoryItemResponse,
    BuildHistoryResponse,
    WorkspaceLogResponse,
    WorkspaceLogsResponse,
)

router = APIRouter(tags=["workspace"])


@router.get(
    "/build-history",
    response_model=BuildHistoryResponse,
    responses=error_responses(500, 503),
    summary="List workspace build history",
)
async def build_history(request: Request) -> BuildHistoryResponse:
    workspace = _workspace(request)
    try:
        records = workspace.list_builds()
    except WorkspaceError as exc:
        raise APIError(500, "WORKSPACE_ERROR", "Build history could not be loaded") from exc
    builds = [
        BuildHistoryItemResponse(
            execution_id=str(record.get("execution_id", "")),
            timestamp=str(_metadata(record).get("updated_at") or _metadata(record).get("created_at") or ""),
            board=str(_metadata(record).get("target_board") or "UNKNOWN"),
            status=str(_metadata(record).get("status") or "UNKNOWN"),
            duration_ms=_duration(_metadata(record)),
        )
        for record in records
    ]
    return BuildHistoryResponse(builds=list(reversed(builds)), count=len(builds))


@router.get(
    "/logs",
    response_model=WorkspaceLogsResponse,
    responses=error_responses(404, 500, 503),
    summary="List workspace logs",
)
async def logs(
    request: Request,
    execution_id: str | None = Query(default=None),
) -> WorkspaceLogsResponse:
    workspace = _workspace(request)
    try:
        paths = workspace.list_logs(execution_id)
        entries = [
            WorkspaceLogResponse(
                execution_id=path.parent.name,
                log_type=path.stem,
                path=str(path),
                content=path.read_text(encoding="utf-8"),
            )
            for path in paths
            if path.is_file() and not path.is_symlink()
        ]
    except WorkspaceError as exc:
        raise APIError(404, "WORKSPACE_LOGS_NOT_FOUND", "Logs could not be loaded") from exc
    except OSError as exc:
        raise APIError(500, "WORKSPACE_ERROR", "Logs could not be read") from exc
    return WorkspaceLogsResponse(logs=list(reversed(entries)), count=len(entries))


def _workspace(request: Request) -> WorkspaceManager:
    workspace = required_state(request, "workspace_manager", "workspace manager")
    assert isinstance(workspace, WorkspaceManager)
    return workspace


def _metadata(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata", {})
    return metadata if isinstance(metadata, dict) else {}


def _duration(metadata: dict[str, Any]) -> float | None:
    value = metadata.get("duration_ms")
    if isinstance(value, int | float):
        return float(value)
    return None
