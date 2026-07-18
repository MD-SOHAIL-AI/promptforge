"""Deprecated read-through facade for the historical ProviderRegistry surface."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from backend.domain_contracts import ConnectionStatus
from backend.model_router.registry import ProviderRegistry
from .registry import AuthState, ConnectionRegistry, CredentialStatus


class LegacyProviderRegistryFacade:
    """Preserve legacy shapes while delegating all auth/status authority.

    Static model metadata and route configuration remain in ProviderRegistry during
    compatibility migration. Credentials, authentication, enablement, and live
    connection health are always projected from ConnectionRegistry.
    """

    deprecated = True

    def __init__(self, legacy: ProviderRegistry, connections: ConnectionRegistry | None = None, **connection_kwargs: Any) -> None:
        self.legacy = legacy
        self.connections = connections or ConnectionRegistry(legacy, **connection_kwargs)
        self.storage = legacy.storage

    def __getattr__(self, name: str) -> Any:
        return getattr(self.legacy, name)

    def configure_provider(self, provider_id: str, settings: dict[str, Any]):
        values = dict(settings)
        api_key_supplied = "api_key" in values
        api_key = values.pop("api_key", None)
        connection_id = f"{provider_id}.default"
        if api_key_supplied:
            if isinstance(api_key, str) and api_key.strip():
                self.connections.connect(
                    provider_id,
                    api_key=api_key,
                    enabled=bool(values.get("enabled", True)),
                )
            elif connection_id in {record.connection_id for record in self.connections.list()}:
                self.connections.disconnect(connection_id)
        if values:
            # ProviderRegistry retains only non-secret model/route preferences.
            self.legacy.configure_provider(provider_id, values)
        if "enabled" in values:
            self.connections.set_enabled(connection_id, bool(values["enabled"]))
        return self.provider(provider_id)

    def api_key(self, provider_id: str) -> str | None:
        return self.connections.transport_credential(f"{provider_id}.default")

    def save_health(self, provider_id: str, health: dict[str, Any]) -> None:
        self.connections.record_transport_result(provider_id, health)

    def provider(self, provider_id: str):
        legacy = self.legacy.provider(provider_id)
        record = self.connections.get_for_provider(provider_id)
        configured = record.credential_status in {CredentialStatus.PRESENT, CredentialStatus.NOT_REQUIRED}
        health_status = {
            ConnectionStatus.READY: "ready",
            ConnectionStatus.DEGRADED: "degraded",
            ConnectionStatus.OFFLINE: "offline",
            ConnectionStatus.ERROR: "error",
            ConnectionStatus.UNKNOWN: "unknown",
        }[record.transport_status]
        if record.credential_status is CredentialStatus.MISSING:
            health_status = "not_configured"
        elif record.auth_state is AuthState.AUTHENTICATION_FAILED:
            health_status = "error"
        elif record.auth_state is AuthState.AUTH_UNKNOWN and configured and record.auth_type.value != "local_no_auth":
            health_status = "auth_unknown"
        return replace(
            legacy,
            configured=configured,
            enabled=record.enabled,
            health_status=health_status,
            last_checked_at=record.status_checked_at,
            last_error=record.safe_message,
        )

    def list_providers(self):
        return [self.provider(provider_id) for provider_id in self.legacy.provider_ids()]

    def list_models(self, provider_id: str):
        return self.legacy.list_models(provider_id)