"""Detected device endpoint schemas."""

from __future__ import annotations

from pydantic import Field

from .common import APIModel


class DetectedBoardResponse(APIModel):
    board_type: str
    port: str
    vid: int | None = None
    pid: int | None = None
    manufacturer: str | None = None
    description: str | None = None
    serial_number: str | None = None


class DetectedBoardsResponse(APIModel):
    boards: list[DetectedBoardResponse] = Field(default_factory=list)
    count: int
