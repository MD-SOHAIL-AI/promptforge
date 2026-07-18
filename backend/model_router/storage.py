"""Non-secret provider settings plus OS-backed credential references."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .credentials import CredentialStore, CredentialStoreError, default_credential_store


SETTINGS_PATH_ENV = "FORGEX_MODEL_ROUTER_SETTINGS_PATH"
logger = logging.getLogger(__name__)


def _safe_defaults() -> dict[str, Any]:
    return {"providers": {}, "routes": {}, "health": {}, "models": {}}


class ProviderSettingsStorage:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else _default_settings_path()
        self.credential_store = credential_store or default_credential_store()
        self.last_read_warning: str | None = None
        self.last_corrupt_backup: Path | None = None
        self._corrupt_backup_attempted = False

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            self.last_read_warning = None
            return _safe_defaults()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            return self._corrupt_defaults(type(exc).__name__)
        except OSError as exc:
            self.last_read_warning = f"model_router_settings_read_failed:{type(exc).__name__}"
            logger.warning(
                "Unable to read model-router settings from %s; using safe defaults (%s)",
                self.path,
                type(exc).__name__,
            )
            return _safe_defaults()
        if not isinstance(data, dict):
            return self._corrupt_defaults("invalid_root")
        self.last_read_warning = None
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
        serialized = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to clean up temporary model-router settings file %s", temporary)
            raise

    def _corrupt_defaults(self, reason: str) -> dict[str, Any]:
        backup = self._backup_corrupt_file()
        backup_status = str(backup) if backup is not None else "backup_unavailable"
        self.last_read_warning = f"model_router_settings_corrupt:{reason}:{backup_status}"
        logger.warning(
            "Corrupt model-router settings detected at %s; using safe defaults (reason=%s, backup=%s)",
            self.path,
            reason,
            backup_status,
        )
        return _safe_defaults()

    def _backup_corrupt_file(self) -> Path | None:
        if self._corrupt_backup_attempted:
            return self.last_corrupt_backup
        self._corrupt_backup_attempted = True
        base = self.path.with_name(self.path.name + ".corrupt")
        backup = base
        if backup.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            counter = 1
            while backup.exists():
                backup = self.path.with_name(
                    f"{self.path.name}.corrupt-{stamp}-{counter}"
                )
                counter += 1
        try:
            shutil.copy2(self.path, backup)
        except OSError as exc:
            logger.warning(
                "Unable to back up corrupt model-router settings at %s (%s)",
                self.path,
                type(exc).__name__,
            )
            return None
        self.last_corrupt_backup = backup
        return backup

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
