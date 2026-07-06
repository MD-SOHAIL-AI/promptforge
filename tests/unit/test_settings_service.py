from __future__ import annotations

from pathlib import Path

import pytest

from backend.settings.service import SettingsService, SettingsValidationError


def test_settings_defaults_load(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")

    settings = service.get_settings()

    assert settings["appearance.theme"] == "forgex-dark"
    assert settings["hardware.confirm_before_flash"] is True


def test_settings_patch_persists_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = SettingsService(path)

    updated = service.patch_settings({"appearance.theme": "light", "editor.tab_size": 4})

    assert updated["appearance.theme"] == "light"
    assert SettingsService(path).get_settings()["editor.tab_size"] == 4


def test_settings_reset_restores_defaults(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    service.patch_settings({"appearance.theme": "light"})

    reset = service.reset()

    assert reset["appearance.theme"] == "forgex-dark"


def test_settings_export_excludes_secrets(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")
    service.patch_settings({"appearance.theme": "forgex-midnight"})

    exported = service.export_settings()

    assert exported["appearance.theme"] == "forgex-midnight"
    assert "sk-test-secret" not in str(exported)


def test_invalid_setting_key_is_rejected(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")

    with pytest.raises(SettingsValidationError):
        service.patch_settings({"model_router.api_key": "secret"})


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not-json", encoding="utf-8")

    settings = SettingsService(path).get_settings()

    assert settings["appearance.theme"] == "forgex-dark"
