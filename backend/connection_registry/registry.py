"""Connection registry wrapping model providers and official CLI status helpers.

Authentication is observational state only. It never grants agent execution,
routing, workspace, approval, or secret-access permission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping

from backend.bridges.codex_status import CodexStatusService
from backend.bridges.detection import BridgeDetectionService
from backend.domain_contracts import ConnectionAuthType, ConnectionStatus, ProviderType
from backend.model_router.credentials import CredentialStore, CredentialStoreError
from backend.model_router.registry import ProviderDefinition, ProviderRegistry

_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SECRET_NAMES = ("api_key", "apikey", "authorization", "credential", "password", "secret", "token")

class AuthState(str, Enum):
    AUTHENTICATED = "authenticated"
    AUTHENTICATION_FAILED = "authentication_failed"
    SIGNED_OUT = "signed_out"
    AUTH_UNKNOWN = "auth_unknown"
    NOT_APPLICABLE = "not_applicable"

class CredentialStatus(str, Enum):
    PRESENT = "credential_present"
    MISSING = "credential_missing"
    NOT_REQUIRED = "not_required"
    UNKNOWN = "credential_unknown"

class ConnectionRegistryFailure(str, Enum):
    NOT_FOUND = "CONNECTION_NOT_FOUND"
    INVALID_REQUEST = "CONNECTION_INVALID_REQUEST"
    CONNECT_UNSUPPORTED = "CONNECTION_CONNECT_UNSUPPORTED"
    DISCONNECT_UNSUPPORTED = "CONNECTION_DISCONNECT_UNSUPPORTED"
    CREDENTIAL_STORE_FAILED = "CONNECTION_CREDENTIAL_STORE_FAILED"
    DISCOVERY_FAILED = "CONNECTION_DISCOVERY_FAILED"

class ConnectionRegistryError(RuntimeError):
    def __init__(self, code: ConnectionRegistryFailure, safe_message: str):
        self.code, self.safe_message = code, safe_message
        super().__init__(safe_message)
    def to_safe_dict(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.safe_message}

def _credential_key(connection_id: str) -> str:
    return connection_id.replace(".", "__")

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _safe(value: Any, key: str = "") -> Any:
    if any(marker in key.casefold() for marker in _SECRET_NAMES):
        return "[REDACTED]"
    if isinstance(value, Enum): return value.value
    if isinstance(value, Mapping): return {str(k): _safe(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)): return [_safe(v) for v in value]
    return value

@dataclass(frozen=True, slots=True)
class ConnectionRecord:
    connection_id: str
    provider_id: str
    account_name: str
    display_name: str
    provider_type: ProviderType
    auth_type: ConnectionAuthType
    auth_state: AuthState
    transport_status: ConnectionStatus
    detected: bool
    enabled: bool
    status_source: str
    status_checked_at: str | None = None
    version: str | None = None
    safe_message: str | None = None
    credential_status: CredentialStatus = CredentialStatus.UNKNOWN
    diagnostic_code: str = "status_not_checked"
    metadata: Mapping[str, Any] = field(default_factory=dict, repr=False)
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("connection_id", "provider_id"):
            if not _ID.fullmatch(getattr(self, name)):
                raise ConnectionRegistryError(ConnectionRegistryFailure.INVALID_REQUEST, f"Invalid {name}.")
        if not self.account_name.strip():
            raise ConnectionRegistryError(ConnectionRegistryFailure.INVALID_REQUEST, "Account name is required.")

    @property
    def authenticated(self) -> bool:
        return self.auth_state in {AuthState.AUTHENTICATED, AuthState.NOT_APPLICABLE}

    @property
    def connection_ready(self) -> bool:
        return bool(
            self.detected
            and self.authenticated
            and self.transport_status is ConnectionStatus.READY
        )

    def to_safe_dict(self) -> dict[str, Any]:
        # Deliberately contains no execution/routing permission field.
        return _safe({
            "schema_version": self.schema_version, "connection_id": self.connection_id,
            "provider_id": self.provider_id, "account_name": self.account_name,
            "display_name": self.display_name, "provider_type": self.provider_type,
            "auth_type": self.auth_type, "credential_status": self.credential_status,
            "auth_state": self.auth_state, "authentication_status": self.auth_state,
            "authenticated": self.authenticated, "transport_status": self.transport_status,
            "connection_health": self.transport_status, "connection_ready": self.connection_ready,
            "detected": self.detected, "enabled": self.enabled, "status_source": self.status_source,
            "status_checked_at": self.status_checked_at, "version": self.version,
            "safe_message": self.safe_message, "diagnostic_code": self.diagnostic_code,
            "metadata": self.metadata,
        })

class ConnectionRegistry:
    CODEX_ID = "codex.default"
    AGY_ID = "antigravity.default"

    def __init__(self, model_registry: ProviderRegistry, *, codex_status_service: CodexStatusService | None = None,
                 codex_login_helper: Any | None = None, bridge_detection: BridgeDetectionService | None = None) -> None:
        self.model_registry = model_registry
        self.credential_store: CredentialStore = model_registry.storage.credential_store
        self.codex_status_service = codex_status_service or CodexStatusService()
        self.codex_login_helper = codex_login_helper
        self.bridge_detection = bridge_detection or BridgeDetectionService()
        self._records: dict[str, ConnectionRecord] = {}
        self.model_registry.bind_canonical_credential_reader(lambda provider_id: self._read_credential(f"{provider_id}.default", provider_id, "default"))
        self.model_registry.bind_canonical_health_writer(lambda provider_id, health: self.record_transport_result(provider_id, health))
        self._bootstrap_model_connections()

    def _read_credential(self, connection_id: str, provider_id: str, account_name: str) -> str | None:
        """Read transport credentials only through the canonical authority."""
        value = self.credential_store.get(_credential_key(connection_id))
        if value:
            return value
        # Compatibility for credentials stored before named connections existed.
        if account_name == "default":
            return self.credential_store.get(provider_id)
        return None

    def transport_credential(self, connection_id: str) -> str | None:
        """Internal transport accessor; never exposed by diagnostics or status APIs."""
        record = self.get(connection_id)
        if record.auth_type is not ConnectionAuthType.API_KEY:
            return None
        return self._read_credential(record.connection_id, record.provider_id, record.account_name)

    def get_for_provider(self, provider_id: str, *, account_name: str = "default") -> ConnectionRecord:
        return self.get(f"{provider_id}.{account_name}")

    def _bootstrap_model_connections(self) -> None:
        for provider_id in self.model_registry.provider_ids():
            definition = self.model_registry.definition(provider_id)
            if provider_id == "codex":
                continue
            connection_id = f"{provider_id}.default"
            provider = self.model_registry.provider(provider_id)
            no_auth = definition.auth_type == "none"
            has_secret = bool(self._read_credential(connection_id, provider_id, "default")) if not definition.local else False
            credential_status = CredentialStatus.NOT_REQUIRED if no_auth else (CredentialStatus.PRESENT if has_secret else CredentialStatus.MISSING)
            diagnostic_code = "local_no_auth" if no_auth else ("credential_present_auth_unverified" if has_secret else "credential_missing")
            self._records[connection_id] = ConnectionRecord(
                connection_id, provider_id, "default", definition.display_name,
                ProviderType.LOCAL_SERVER if definition.local else ProviderType.REMOTE_API,
                ConnectionAuthType.LOCAL_NO_AUTH if no_auth else ConnectionAuthType.API_KEY,
                AuthState.NOT_APPLICABLE if no_auth else AuthState.AUTH_UNKNOWN,
                self._map_health(provider.health_status) if no_auth else ConnectionStatus.UNKNOWN,
                bool(definition.available), provider.enabled,
                "connection_registry_bootstrap", safe_message=(
                    None if no_auth else ("Credential stored; authentication has not been verified." if has_secret else "Credential is not configured.")
                ), credential_status=credential_status, diagnostic_code=diagnostic_code,
            )
        self._records[self.CODEX_ID] = self._cli_record(self.CODEX_ID, "codex", "Codex CLI", "default")
        self._records[self.AGY_ID] = self._cli_record(self.AGY_ID, "antigravity", "Google Antigravity / AGY CLI", "default")
    @staticmethod
    def _map_health(value: str) -> ConnectionStatus:
        return {"connected":ConnectionStatus.READY,"ready":ConnectionStatus.READY,"offline":ConnectionStatus.OFFLINE,
                "error":ConnectionStatus.ERROR,"degraded":ConnectionStatus.DEGRADED}.get(value, ConnectionStatus.UNKNOWN)

    @staticmethod
    def _cli_record(connection_id: str, provider_id: str, display_name: str, account_name: str) -> ConnectionRecord:
        return ConnectionRecord(connection_id, provider_id, account_name, display_name, ProviderType.LOCAL_CLI,
            ConnectionAuthType.CLI_OWNED_SESSION, AuthState.AUTH_UNKNOWN, ConnectionStatus.UNKNOWN, False, False,
            "official_status_not_checked", safe_message="Authentication has not been safely verified.",
            credential_status=CredentialStatus.UNKNOWN, diagnostic_code="auth_unknown")

    def list(self) -> tuple[ConnectionRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def get(self, connection_id: str) -> ConnectionRecord:
        try: return self._records[connection_id]
        except KeyError as exc: raise ConnectionRegistryError(ConnectionRegistryFailure.NOT_FOUND, "Connection not found.") from exc

    def discover(self) -> tuple[ConnectionRecord, ...]:
        self.refresh_status(self.CODEX_ID)
        self.refresh_status(self.AGY_ID)
        return self.list()

    def connect(self, provider_id: str, *, account_name: str = "default", api_key: str | None = None,
                enabled: bool = True, launch_official_login: bool = False) -> ConnectionRecord:
        if not _ID.fullmatch(provider_id) or not _ID.fullmatch(account_name):
            raise ConnectionRegistryError(ConnectionRegistryFailure.INVALID_REQUEST, "Provider or account name is invalid.")
        connection_id = f"{provider_id}.{account_name}"
        if provider_id == "codex":
            if self.codex_login_helper is None:
                raise ConnectionRegistryError(ConnectionRegistryFailure.CONNECT_UNSUPPORTED, "Codex login helper is unavailable.")
            self.codex_login_helper.launch(confirm_launch_codex_login=launch_official_login)
            self._records.setdefault(connection_id, self._cli_record(connection_id, provider_id, "Codex CLI", account_name))
            return self.refresh_status(connection_id)
        if provider_id in {"antigravity", "agy"}:
            raise ConnectionRegistryError(ConnectionRegistryFailure.CONNECT_UNSUPPORTED, "Use the official AGY CLI login flow; ForgeX does not inspect its auth files.")
        definition = self.model_registry.definition(provider_id)
        if definition.local or definition.auth_type != "api_key" or not isinstance(api_key, str) or not api_key.strip():
            raise ConnectionRegistryError(ConnectionRegistryFailure.INVALID_REQUEST, "A non-empty API key is required.")
        try: self.credential_store.set(_credential_key(connection_id), api_key.strip())
        except CredentialStoreError as exc: raise ConnectionRegistryError(ConnectionRegistryFailure.CREDENTIAL_STORE_FAILED, "The OS credential store could not save this credential.") from exc
        record = ConnectionRecord(connection_id, provider_id, account_name, definition.display_name, ProviderType.REMOTE_API,
            ConnectionAuthType.API_KEY, AuthState.AUTH_UNKNOWN, ConnectionStatus.UNKNOWN, True, enabled,
            "os_credential_store", _now(), safe_message="Credential stored; authentication has not been verified.",
            credential_status=CredentialStatus.PRESENT, diagnostic_code="credential_present_auth_unverified")
        self._records[connection_id] = record
        return record

    def refresh_status(self, connection_id: str) -> ConnectionRecord:
        current = self.get(connection_id)
        if current.provider_id == "codex":
            try:
                status = self.codex_status_service.status()
            except Exception:
                updated = replace(current, auth_state=AuthState.AUTH_UNKNOWN,
                    transport_status=ConnectionStatus.UNKNOWN, status_source="official_status_unavailable",
                    status_checked_at=_now(), safe_message="Authentication could not be safely verified.",
                    diagnostic_code="auth_unknown")
            else:
                auth = {"signed_in":AuthState.AUTHENTICATED,"signed_out":AuthState.SIGNED_OUT}.get(status.auth_status, AuthState.AUTH_UNKNOWN)
                updated = replace(current, detected=status.codex_installed, auth_state=auth,
                    credential_status=CredentialStatus.UNKNOWN,
                    transport_status=ConnectionStatus.READY if status.codex_installed else ConnectionStatus.OFFLINE,
                    status_source="official_codex_login_status", status_checked_at=_now(), version=status.codex_version,
                    safe_message=None if auth is AuthState.AUTHENTICATED else ("Signed out." if auth is AuthState.SIGNED_OUT else "Authentication could not be safely verified."),
                    diagnostic_code="authenticated" if auth is AuthState.AUTHENTICATED else ("signed_out" if auth is AuthState.SIGNED_OUT else "auth_unknown"))
        elif current.provider_id == "antigravity":
            try:
                result = self.bridge_detection.detect_provider("antigravity_cli_bridge")
            except Exception:
                updated = replace(current, auth_state=AuthState.AUTH_UNKNOWN,
                    transport_status=ConnectionStatus.UNKNOWN, status_source="official_status_unavailable",
                    status_checked_at=_now(), safe_message="Authentication could not be safely verified.",
                    diagnostic_code="auth_unknown")
            else:
                # AGY is authenticated only when the bounded detector has high-confidence evidence.
                auth = AuthState.AUTHENTICATED if result.auth_status == "authenticated" and result.status_confidence == "high" else AuthState.AUTH_UNKNOWN
                updated = replace(current, detected=result.installed, auth_state=auth,
                    credential_status=CredentialStatus.UNKNOWN,
                    transport_status=ConnectionStatus.READY if result.installed else ConnectionStatus.OFFLINE,
                    status_source="official_cli_status" if auth is AuthState.AUTHENTICATED else "official_status_unavailable",
                    status_checked_at=_now(), version=result.version,
                    safe_message=None if auth is AuthState.AUTHENTICATED else "Authentication could not be safely verified.",
                    diagnostic_code="authenticated" if auth is AuthState.AUTHENTICATED else "auth_unknown")
        elif current.auth_type is ConnectionAuthType.API_KEY:
            try:
                present = bool(self._read_credential(connection_id, current.provider_id, current.account_name))
            except CredentialStoreError:
                present = False
                credential_status = CredentialStatus.UNKNOWN
            else:
                credential_status = CredentialStatus.PRESENT if present else CredentialStatus.MISSING
            retained_auth = current.auth_state if present and current.auth_state in {AuthState.AUTHENTICATED, AuthState.AUTHENTICATION_FAILED} else AuthState.AUTH_UNKNOWN
            settings = self.model_registry.storage.provider_settings(current.provider_id)
            enabled = bool(settings.get("enabled", current.enabled or present))
            updated = replace(current, detected=True, enabled=enabled, auth_state=retained_auth,
                credential_status=credential_status, status_checked_at=_now(), status_source="connection_registry_credential_check",
                transport_status=current.transport_status if present else ConnectionStatus.UNKNOWN,
                safe_message=("Credential stored; authentication has not been verified." if present and retained_auth is AuthState.AUTH_UNKNOWN else ("Credential is not configured." if not present else current.safe_message)),
                diagnostic_code=("credential_missing" if credential_status is CredentialStatus.MISSING else ("credential_status_unknown" if credential_status is CredentialStatus.UNKNOWN else ("credential_present_auth_unverified" if retained_auth is AuthState.AUTH_UNKNOWN else retained_auth.value))))
        else:
            updated = replace(current, status_checked_at=_now())
        self._records[connection_id] = updated
        return updated

    def record_transport_result(self, provider_id: str, result: Mapping[str, Any] | Any) -> ConnectionRecord:
        """Record an explicit provider check as the canonical auth/health result."""
        current = self.get_for_provider(provider_id)
        if isinstance(result, Mapping):
            ok = bool(result.get("ok"))
            status = str(result.get("status") or "unknown")
            error_code = str(result.get("error_code") or "")
            message = result.get("message") if isinstance(result.get("message"), str) else None
        else:
            ok = bool(getattr(result, "ok", False))
            status = str(getattr(result, "status", "unknown") or "unknown")
            error_code = str(getattr(result, "error_code", "") or "")
            message = getattr(result, "message", None) if isinstance(getattr(result, "message", None), str) else None
        credential_status = current.credential_status
        if current.auth_type is ConnectionAuthType.API_KEY:
            credential_status = CredentialStatus.PRESENT if self.transport_credential(current.connection_id) else CredentialStatus.MISSING
        normalized = f"{status} {error_code}".casefold()
        auth_failure = any(marker in normalized for marker in ("auth", "credential", "unauthorized", "forbidden", "401", "403"))
        if credential_status is CredentialStatus.MISSING:
            auth_state = AuthState.AUTH_UNKNOWN
            health = ConnectionStatus.UNKNOWN
            diagnostic = "credential_missing"
            safe_message = "Credential is not configured."
        elif ok:
            auth_state = AuthState.NOT_APPLICABLE if current.auth_type is ConnectionAuthType.LOCAL_NO_AUTH else AuthState.AUTHENTICATED
            health = ConnectionStatus.READY
            diagnostic = "authenticated" if auth_state is AuthState.AUTHENTICATED else "healthy"
            safe_message = None
        elif auth_failure:
            auth_state = AuthState.AUTHENTICATION_FAILED
            health = ConnectionStatus.ERROR
            diagnostic = "authentication_failed"
            safe_message = "Provider authentication failed."
        else:
            auth_state = current.auth_state if current.auth_state is AuthState.AUTHENTICATED else (AuthState.NOT_APPLICABLE if current.auth_type is ConnectionAuthType.LOCAL_NO_AUTH else AuthState.AUTH_UNKNOWN)
            health = self._map_health(status)
            if health is ConnectionStatus.UNKNOWN:
                health = ConnectionStatus.ERROR if status.casefold() in {"error", "unavailable"} else ConnectionStatus.UNKNOWN
            diagnostic = "unhealthy" if health in {ConnectionStatus.ERROR, ConnectionStatus.OFFLINE} else "health_unknown"
            safe_message = "Provider connection is unavailable." if message else None
        updated = replace(current, credential_status=credential_status, auth_state=auth_state,
            transport_status=health, detected=True, status_source="connection_registry_transport_check",
            status_checked_at=_now(), safe_message=safe_message, diagnostic_code=diagnostic)
        self._records[current.connection_id] = updated
        return updated

    def record_transport_success(self, provider_id: str) -> ConnectionRecord:
        return self.record_transport_result(provider_id, {"ok": True, "status": "ready"})
    def set_enabled(self, connection_id: str, enabled: bool) -> ConnectionRecord:
        current = self.get(connection_id)
        updated = replace(current, enabled=bool(enabled), status_checked_at=_now(),
            diagnostic_code=("disabled" if not enabled else current.diagnostic_code))
        self._records[connection_id] = updated
        return updated

    def disconnect(self, connection_id: str) -> ConnectionRecord:
        current = self.get(connection_id)
        if current.auth_type is not ConnectionAuthType.API_KEY:
            raise ConnectionRegistryError(ConnectionRegistryFailure.DISCONNECT_UNSUPPORTED, "Disconnect through the provider's official CLI.")
        try:
            self.credential_store.delete(_credential_key(connection_id))
            if current.account_name == "default":
                self.credential_store.delete(current.provider_id)
        except CredentialStoreError as exc:
            raise ConnectionRegistryError(ConnectionRegistryFailure.CREDENTIAL_STORE_FAILED, "The OS credential store could not remove this credential.") from exc
        updated = replace(current, auth_state=AuthState.AUTH_UNKNOWN, detected=True, enabled=False,
            credential_status=CredentialStatus.MISSING, transport_status=ConnectionStatus.UNKNOWN,
            status_source="os_credential_store", status_checked_at=_now(), safe_message="Credential removed.",
            diagnostic_code="credential_missing")
        self._records[connection_id] = updated
        return updated

    def capabilities(self, connection_id: str) -> dict[str, Any]:
        record = self.get(connection_id)
        if record.provider_id in {"codex", "antigravity"}:
            return {"connection_id":connection_id,"transport":"local_cli","authentication_observable":record.auth_state not in {AuthState.AUTH_UNKNOWN, AuthState.AUTHENTICATION_FAILED},"agent_execution_permitted":False}
        definition = self.model_registry.definition(record.provider_id)
        return {"connection_id":connection_id,"transport":"local_http" if definition.local else "remote_https","authentication_observable":True,"agent_execution_permitted":False}

    def models(self, connection_id: str) -> list[dict[str, Any]]:
        record = self.get(connection_id)
        if record.provider_id in {"antigravity", "codex"}: return []
        return [model.to_dict() for model in self.model_registry.list_models(record.provider_id)]

    def safe_diagnostics(self, connection_id: str | None = None) -> dict[str, Any]:
        records = (self.get(connection_id),) if connection_id else self.list()
        return {"schema_version":1,"connections":[record.to_safe_dict() for record in records],
                "credential_backend":type(self.credential_store).__name__,"auth_files_read":False,
                "authentication_grants_execution":False}



