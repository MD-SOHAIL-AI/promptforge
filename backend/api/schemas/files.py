"""Workspace file route schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import APIModel


class WorkspaceEntryResponse(APIModel):
    path: str
    kind: Literal["file", "folder"]
    file_type: str | None = None


class ProjectFilesResponse(APIModel):
    project_id: str
    project_name: str
    entries: list[WorkspaceEntryResponse]


class FileContentResponse(APIModel):
    project_id: str
    path: str
    content: str
    file_type: str


class FileCreateRequest(APIModel):
    project_id: str
    path: str
    kind: Literal["file", "folder"] = "file"
    content: str = ""
    file_type: str | None = None


class FileUpdateRequest(APIModel):
    project_id: str
    path: str
    content: str | None = None
    new_path: str | None = None
    kind: Literal["file", "folder"] = "file"
    file_type: str | None = None


class FileDeleteResponse(APIModel):
    project_id: str
    path: str
    deleted: bool
    deleted_count: int = Field(ge=0)
