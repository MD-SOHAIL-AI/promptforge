"""Non-secret provider settings plus OS-backed credential references."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .credentials import CredentialStore, CredentialStoreError, default_credential_store


SETTINGS_PATH_ENV = "FORGEX_MODEL_ROUTER_SETTINGS_PATH"


class ProviderSettingsStorage:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else _default_settings_path()
        self.credential_store = credential_store or default_credential_store()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"providers": {}, "routes": {}, "health": {}, "models": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"providers": {}, "routes": {}, "health": {}, "models": {}}
        if not isinstance(data, dict):
            return {"providers": {}, "routes": {}, "health": {}, "models": {}}
        providers = data.get("providers")
        routes = data.get("routes")
        health = data.get("health")
        models = data.get("models")
        return {
            "providers": providers if isinstance(providers, dict) else {},
            "routes": routes if isinstance(routes, dict) else {},
            "health": health if isinstance(health, dict) else {},
            "models": models if isinstance(models, dict) else {},
        }

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "providers": data.get("providers", {}),
            "routes": data.get("routes", {}),
            "health": data.get("health", {}),
            "models": data.get("models", {}),
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def provider_settings(self, provider_id: str) -> dict[str, Any]:
        providers = self.read()["providers"]
        value = providers.get(provider_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def save_provider_settings(
        self,
        provider_id: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        data = self.read()
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
        # Remove/migrate legacy plaintext before writing normal settings.
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
        self.write(data)
        return dict(next_settings)

    def api_key(self, provider_id: str) -> str | None:
        settings = self.provider_settings(provider_id)
        legacy = settings.get("api_key")
        if isinstance(legacy, str) and legacy.strip():
            # One-time migration from the historical plaintext JSON format.
            self.credential_store.set(provider_id, legacy.strip())
            self.save_provider_settings(provider_id, {"api_key": legacy.strip()})
        return self.credential_store.get(provider_id)

    def save_provider_health(
        self,
        provider_id: str,
        health: dict[str, Any],
    ) -> None:
        data = self.read()
        records = dict(data["health"])
        records[provider_id] = dict(health)
        data["health"] = records
        self.write(data)

    def provider_health(self, provider_id: str) -> dict[str, Any]:
        records = self.read()["health"]
        value = records.get(provider_id, {})
        return dict(value) if isinstance(value, dict) else {}

    def save_provider_models(
        self,
        provider_id: str,
        models: list[dict[str, Any]],
    ) -> None:
        data = self.read()
        records = dict(data["models"])
        records[provider_id] = [dict(model) for model in models]
        data["models"] = records
        self.write(data)

    def provider_models(self, provider_id: str) -> list[dict[str, Any]]:
        records = self.read()["models"]
        value = records.get(provider_id, [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def routes(self) -> dict[str, dict[str, Any]]:
        routes = self.read()["routes"]
        return {
            str(key): dict(value)
            for key, value in routes.items()
            if isinstance(value, dict)
        }

    def save_route(self, task_type: str, route: dict[str, Any]) -> None:
        data = self.read()
        routes = dict(data["routes"])
        routes[task_type] = dict(route)
        data["routes"] = routes
        self.write(data)


def mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    prefix = value[:3]
    suffix = value[-4:]
    return f"{prefix}-...{suffix}" if not prefix.endswith("-") else f"{prefix}...{suffix}"


def _default_settings_path() -> Path:
    override = os.getenv(SETTINGS_PATH_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    appdata = os.getenv("APPDATA")
    if appdata and appdata.strip():
        return Path(appdata) / "ForgeX" / "model-router" / "settings.json"
    return Path.home() / ".forgex" / "model-router" / "settings.json"
