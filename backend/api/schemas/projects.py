"""Project route schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from .common import APIModel


class ProjectFileResponse(APIModel):
    path: str
    content: str
    file_type: str


class ProjectResponse(APIModel):
    project_id: str
    project_name: str
    target_board: str
    framework: str
    project_path: str
    external: bool = False
    project_type: str = "generated"
    created_at: datetime
    updated_at: datetime
    file_count: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    files: list[ProjectFileResponse] = Field(default_factory=list)


class ProjectListResponse(APIModel):
    projects: list[ProjectResponse]
    count: int = Field(ge=0)


class ProjectDeleteResponse(APIModel):
    project_id: str
    deleted: bool
