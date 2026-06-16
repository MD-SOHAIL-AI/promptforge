"""Health endpoint schemas."""

from datetime import datetime

from .common import APIModel


class ServiceHealth(APIModel):
    workflow: bool
    planner: bool
    coordinator: bool


class HealthResponse(APIModel):
    status: str
    version: str
    timestamp: datetime
    services: ServiceHealth
