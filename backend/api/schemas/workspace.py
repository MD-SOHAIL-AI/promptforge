"""Workspace history and log route schemas."""

from __future__ import annotations

from .common import APIModel


class BuildHistoryItemResponse(APIModel):
    execution_id: str
    timestamp: str
    board: str
    status: str
    duration_ms: float | None = None


class BuildHistoryResponse(APIModel):
    builds: list[BuildHistoryItemResponse]
    count: int


class WorkspaceLogResponse(APIModel):
    execution_id: str
    log_type: str
    path: str
    content: str


class WorkspaceLogsResponse(APIModel):
    logs: list[WorkspaceLogResponse]
    count: int
