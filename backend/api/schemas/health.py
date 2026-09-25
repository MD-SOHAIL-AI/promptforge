"""Health endpoint schemas."""

from datetime import datetime

from .common import APIModel


class ServiceHealth(APIModel):
    workflow: bool
    planner: bool
    coordinator: bool


class ToolchainHealth(APIModel):
    available: bool
    executable: str | None = None
    version: str | None = None
    reason: str
    message: str


class HealthResponse(APIModel):
    status: str
    service: str = "forgex-backend"
    version: str
    runtime_contract: str
    source_fingerprint: str
    local_auth_required: bool = False
    local_auth_fingerprint: str | None = None
    timestamp: datetime
    services: ServiceHealth
    platformio: ToolchainHealth | None = None
    harness_configured: bool = False
