"""Backend-owned non-secret settings persistence."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .defaults import SETTINGS_DEFAULTS, SETTINGS_SCHEMA


SETTINGS_PATH_ENV = "FORGEX_SETTINGS_PATH"


class SettingsValidationError(ValueError):
    """Raised when a settings patch contains unsupported or invalid values."""


class SettingsService:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_settings_path()

    def get_settings(self) -> dict[str, Any]:
        stored = self._read_file()
        merged = deepcopy(SETTINGS_DEFAULTS)
        for key, value in stored.items():
            if key in SETTINGS_SCHEMA and self._is_valid_value(key, value):
                merged[key] = value
        return merged

    def get_schema(self) -> dict[str, dict[str, Any]]:
        return deepcopy(SETTINGS_SCHEMA)

    def patch_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(updates, dict):
            raise SettingsValidationError("settings patch must be an object")
        current = self.get_settings()
        for key, value in updates.items():
            self._validate_key_value(str(key), value)
            current[str(key)] = self._normalize_value(str(key), value)
        self._write_file(current)
        return current

    def reset(self) -> dict[str, Any]:
        settings = deepcopy(SETTINGS_DEFAULTS)
        self._write_file(settings)
        return settings

    def export_settings(self) -> dict[str, Any]:
        return self.get_settings()

    def _read_file(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        settings = data.get("settings") if isinstance(data, dict) else None
        return dict(settings) if isinstance(settings, dict) else {}

    def _write_file(self, settings: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"settings": settings}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _validate_key_value(self, key: str, value: Any) -> None:
        if key not in SETTINGS_SCHEMA:
            raise SettingsValidationError(f"Unknown setting key: {key}")
        if not self._is_valid_value(key, value):
            raise SettingsValidationError(f"Invalid value for setting: {key}")

    def _is_valid_value(self, key: str, value: Any) -> bool:
        try:
            self._normalize_value(key, value)
        except SettingsValidationError:
            return False
        return True

    def _normalize_value(self, key: str, value: Any) -> Any:
        schema = SETTINGS_SCHEMA[key]
        value_type = schema["type"]
        if value_type == "boolean":
            if isinstance(value, bool):
                return value
            raise SettingsValidationError(f"{key} must be a boolean")
        if value_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SettingsValidationError(f"{key} must be a number")
            minimum = schema.get("min")
            maximum = schema.get("max")
            if minimum is not None and value < minimum:
                raise SettingsValidationError(f"{key} is below minimum")
            if maximum is not None and value > maximum:
                raise SettingsValidationError(f"{key} is above maximum")
            default = SETTINGS_DEFAULTS[key]
            return int(value) if isinstance(default, int) and not isinstance(default, bool) else value
        if value_type == "select":
            if not isinstance(value, str) or value not in schema.get("options", []):
                raise SettingsValidationError(f"{key} must be one of the supported options")
            return value
        if value_type == "string":
            if isinstance(value, str):
                return value
            raise SettingsValidationError(f"{key} must be a string")
        if value_type == "string_list":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise SettingsValidationError(f"{key} must be a string list")
            return [item for item in value if item.strip()]
        raise SettingsValidationError(f"Unsupported setting type for {key}")


def _default_settings_path() -> Path:
    override = os.getenv(SETTINGS_PATH_ENV)
    if override and override.strip():
        return Path(override).expanduser()
    appdata = os.getenv("APPDATA")
    if appdata and appdata.strip():
        return Path(appdata) / "ForgeX" / "settings" / "settings.json"
    return Path.home() / ".forgex" / "settings" / "settings.json"
