"""Thread-safe, atomic persistence for model-router settings and cache state."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .credentials import CredentialStore, CredentialStoreError, default_credential_store


SETTINGS_PATH_ENV = "FORGEX_MODEL_ROUTER_SETTINGS_PATH"
_EMPTY_STATE: dict[str, dict[str, Any]] = {
    "providers": {},
    "routes": {},
    "health": {},
    "models": {},
}


class ModelRouterStorageError(RuntimeError):
    """Raised when model-router state cannot be read safely."""


class ProviderSettingsStorage:
    """Persist non-secret provider state without lost updates or partial writes.

    Secrets remain in the operating-system credential store. The JSON document is
    protected by an in-process re-entrant lock and every write uses temp-file +
    ``os.replace`` so readers never observe a partially-written document.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else _default_settings_path()
        self.credential_store = credential_store or default_credential_store()
        self._lock = threading.RLock()

    def read(self) -> dict[str, Any]:
        with self._lock:
            return self._read_unlocked()

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._write_unlocked(data)

    def provider_settings(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            providers = self._read_unlocked()["providers"]
            value = providers.get(provider_id, {})
            return dict(value) if isinstance(value, dict) else {}

    def save_provider_settings(
        self,
        provider_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read_unlocked()
            providers = dict(data["providers"])
            current = providers.get(provider_id, {})
            if not isinstance(current, dict):
                current = {}
            next_settings = {**current}
            if "api_key" in settings:
                secret = settings["api_key"]
                if secret is None:
                    self.credential_store.delete(provider_id)
                    next_settings.pop("credential_ref", None)
                elif isinstance(secret, str) and secret.strip():
                    self.credential_store.set(provider_id, secret.strip())
                    next_settings["credential_ref"] = provider_id
                else:
                    raise CredentialStoreError("credential_secret_invalid")
            # Never persist plaintext credentials.
            next_settings.pop("api_key", None)
            for key in ("base_url", "default_model", "enabled"):
                if key in settings:
                    value = settings[key]
                    if value is None:
                        next_settings.pop(key, None)
                    else:
                        next_settings[key] = value
            providers[provider_id] = next_settings
            data["providers"] = providers
            self._write_unlocked(data)
            return dict(next_settings)

    def api_key(self, provider_id: str) -> str | None:
        with self._lock:
            settings = self.provider_settings(provider_id)
            legacy = settings.get("api_key")
            if isinstance(legacy, str) and legacy.strip():
                # Historical plaintext state is migrated immediately and then
                # removed from JSON by save_provider_settings().
                self.credential_store.set(provider_id, legacy.strip())
                self.save_provider_settings(provider_id, {"api_key": legacy.strip()})
            return self.credential_store.get(provider_id)

    def save_provider_health(
        self,
        provider_id: str,
        health: dict[str, Any],
    ) -> None:
        with self._lock:
            data = self._read_unlocked()
            records = dict(data["health"])
            records[provider_id] = dict(health)
            data["health"] = records
            self._write_unlocked(data)

    def provider_health(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            records = self._read_unlocked()["health"]
            value = records.get(provider_id, {})
            return dict(value) if isinstance(value, dict) else {}

    def save_provider_models(
        self,
        provider_id: str,
        models: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            data = self._read_unlocked()
            records = dict(data["models"])
            records[provider_id] = [dict(model) for model in models]
            data["models"] = records
            self._write_unlocked(data)

    def provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        with self._lock:
            records = self._read_unlocked()["models"]
            value = records.get(provider_id, [])
            if not isinstance(value, list):
                return []
            return [dict(item) for item in value if isinstance(item, dict)]

    def routes(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            routes = self._read_unlocked()["routes"]
            return {
                str(key): dict(value)
                for key, value in routes.items()
                if isinstance(value, dict)
            }

    def save_route(self, task_type: str, route: dict[str, Any]) -> None:
        with self._lock:
            data = self._read_unlocked()
            routes = dict(data["routes"])
            routes[task_type] = dict(route)
            data["routes"] = routes
            self._write_unlocked(data)

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Refuse to turn a corrupt settings file into an empty state that a
            # later save could silently overwrite. The original bytes remain on
            # disk for recovery.
            raise ModelRouterStorageError("model_router_settings_invalid_json") from exc
        except OSError as exc:
            raise ModelRouterStorageError("model_router_settings_read_failed") from exc
        if not isinstance(data, dict):
            raise ModelRouterStorageError("model_router_settings_invalid_root")
        return {
            "providers": data.get("providers") if isinstance(data.get("providers"), dict) else {},
            "routes": data.get("routes") if isinstance(data.get("routes"), dict) else {},
            "health": data.get("health") if isinstance(data.get("health"), dict) else {},
            "models": data.get("models") if isinstance(data.get("models"), dict) else {},
        }

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "providers": data.get("providers", {}),
            "routes": data.get("routes", {}),
            "health": data.get("health", {}),
            "models": data.get("models", {}),
        }
        serialized = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        fd: int | None = None
        temporary_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            temporary_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                fd = None
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        except OSError as exc:
            raise ModelRouterStorageError("model_router_settings_write_failed") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    prefix = value[:3]
    suffix = value[-4:]
    return f"{prefix}-...{suffix}" if not prefix.endswith("-") else f"{prefix}...{suffix}"


def _empty_state() -> dict[str, Any]:
    return {key: dict(value) for key, value in _EMPTY_STATE.items()}


def _default_settings_path() -> Path:
    override = os.getenv(SETTINGS_PATH_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    appdata = os.getenv("APPDATA")
    if appdata and appdata.strip():
        return Path(appdata) / "ForgeX" / "model-router" / "settings.json"
    return Path.home() / ".forgex" / "model-router" / "settings.json"
