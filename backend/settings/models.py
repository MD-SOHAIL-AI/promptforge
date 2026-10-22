"""Pydantic models for ForgeX settings APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    settings: dict[str, Any]


class SettingsSchemaResponse(BaseModel):
    schema_: dict[str, dict[str, Any]] = Field(alias="schema")


class SettingsPatchRequest(BaseModel):
    settings: dict[str, Any]


class SettingsExportResponse(BaseModel):
    settings: dict[str, Any]
    secrets_included: bool = False
