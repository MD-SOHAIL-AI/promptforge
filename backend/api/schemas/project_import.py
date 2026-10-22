"""External project import schemas."""

from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, field_validator

from .common import APIModel


class ProjectImportRequest(APIModel):
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("path must be non-empty and NUL-free")
        return value

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"path": "C:\\Users\\diwan\\Projects\\smart-irrigation-system"}},
    )


class PlatformIOEnvironmentResponse(APIModel):
    name: str
    platform: str = ""
    board: str = ""
    framework: str = ""
    monitor_speed: int | None = None
    upload_speed: int | None = None
    lib_deps: list[str] = Field(default_factory=list)


class PlatformIOMetadataResponse(APIModel):
    environments: list[PlatformIOEnvironmentResponse] = Field(default_factory=list)


class ProjectImportResponse(APIModel):
    project_id: str
    name: str
    path: str
    external: bool = True
    project_type: str = "platformio"
    board: str
    framework: str
    platform: str = ""
    environment: str | None = None
    monitor_speed: int | None = None
    upload_speed: int | None = None
    has_platformio_ini: bool = True
    files_loaded: bool = True
    platformio: PlatformIOMetadataResponse
    metadata: dict[str, Any] = Field(default_factory=dict)
