from __future__ import annotations

from pathlib import Path

import pytest

from backend.settings.service import SettingsService, SettingsValidationError


def test_settings_defaults_load(tmp_path: Path) -> None:
    service = SettingsService(tmp_path / "settings.json")

    settings = service.get_settings()

    assert settings["appearance.theme"] == "forgex-dark"
    assert settings["appearance.background_asset_id"] == ""
    assert settings["appearance.background_dim"] == 72
    assert settings["appearance.surface_opacity"] == 94
    assert settings["appearance.contrast"] == "standard"
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


def test_appearance_controls_validate_and_persist(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    service = SettingsService(path)
    asset_id = "background-12345678-1234-4123-8123-123456789abc.webp"

    updated = service.patch_settings(
        {
            "appearance.background_asset_id": asset_id,
            "appearance.background_asset_name": "night sky.webp",
            "appearance.background_dim": 45,
            "appearance.background_blur": 12,
            "appearance.background_fit": "contain",
            "appearance.background_position": "top",
            "appearance.surface_opacity": 80,
            "appearance.contrast": "high",
            "appearance.corner_radius": 16,
            "appearance.glow_intensity": 60,
            "appearance.motion": "full",
        }
    )

    assert updated["appearance.background_asset_id"] == asset_id
    assert SettingsService(path).get_settings()["appearance.background_asset_name"] == "night sky.webp"
    assert updated["appearance.motion"] == "full"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("appearance.background_asset_id", "../../outside.png"),
        ("appearance.background_dim", 101),
        ("appearance.background_blur", 31),
        ("appearance.surface_opacity", 64),
        ("appearance.contrast", "extreme"),
        ("appearance.corner_radius", 21),
        ("appearance.glow_intensity", -1),
        ("appearance.motion", "constant"),
    ],
)
def test_invalid_appearance_controls_are_rejected(tmp_path: Path, key: str, value: object) -> None:
    service = SettingsService(tmp_path / "settings.json")

    with pytest.raises(SettingsValidationError):
        service.patch_settings({key: value})


def test_legacy_minimal_motion_is_migrated_to_off(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"settings":{"appearance.motion":"minimal"}}', encoding="utf-8")

    settings = SettingsService(path).get_settings()

    assert settings["appearance.motion"] == "off"
