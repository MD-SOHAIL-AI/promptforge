"""Central fail-closed authorization for the local generic run API."""

from __future__ import annotations

import os
from dataclasses import dataclass


GENERIC_API_DISABLED_CODE = "generic_execution_disabled"
GENERIC_API_DISABLED_MESSAGE = "Generic agent execution is disabled by local policy."


@dataclass(frozen=True, slots=True)
class GenericRunFeatureFlags:
    agy_bridge_enabled: bool = False
    api_enabled: bool = False
    routing_enabled: bool = False
    agy_provider_enabled: bool = False
    cutover_enabled: bool = False

    @classmethod
    def from_environment(cls, env: dict[str, str] | None = None) -> "GenericRunFeatureFlags":
        source = os.environ if env is None else env

        def enabled(name: str) -> bool:
            return source.get(name, "").strip() == "1"

        return cls(
            agy_bridge_enabled=enabled("FORGEX_ENABLE_AGY_BRIDGE"),
            api_enabled=enabled("FORGEX_ENABLE_GENERIC_BRIDGE_API"),
            routing_enabled=enabled("FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING"),
            agy_provider_enabled=enabled("FORGEX_ENABLE_AGY_GENERIC_PROVIDER"),
            cutover_enabled=enabled("FORGEX_ENABLE_AGY_GENERIC_CUTOVER"),
        )

    @property
    def execution_enabled(self) -> bool:
        return all(
            (
                self.agy_bridge_enabled,
                self.api_enabled,
                self.routing_enabled,
                self.agy_provider_enabled,
                self.cutover_enabled,
            )
        )

    @property
    def disabled_reason(self) -> str | None:
        if self.execution_enabled:
            return None
        return GENERIC_API_DISABLED_MESSAGE

    def require_execution(self, provider_id: str) -> None:
        if provider_id != "agy" or not self.execution_enabled:
            raise GenericRunAuthorizationError()


class GenericRunAuthorizationError(PermissionError):
    code = GENERIC_API_DISABLED_CODE

    def __init__(self) -> None:
        super().__init__(GENERIC_API_DISABLED_MESSAGE)

