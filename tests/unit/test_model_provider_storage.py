from __future__ import annotations

import logging
from pathlib import Path

import pytest

from backend.model_router import MemoryCredentialStore, ProviderRegistry, ProviderSettingsStorage


def test_openrouter_config_saves_without_exposing_raw_key(tmp_path: Path) -> None:
    credentials = MemoryCredentialStore()
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=credentials)
    registry = ProviderRegistry(storage)

    provider = registry.configure_provider(
        "openrouter",
        {
            "api_key": "sk-test-secret-abcd",
            "default_model": "openai/gpt-oss-120b:free",
            "enabled": True,
        },
    )

    assert provider.configured is True
    assert provider.api_key_masked == "sk-...abcd"
    assert "sk-test-secret-abcd" not in provider.to_dict().values()
    assert storage.provider_settings("openrouter")["credential_ref"] == "openrouter"
    assert credentials.get("openrouter") == "sk-test-secret-abcd"
    assert "sk-test-secret-abcd" not in storage.path.read_text(encoding="utf-8")
    assert not storage.path.with_suffix(".json.tmp").exists()


def test_local_provider_config_works_for_ollama_and_lmstudio(tmp_path: Path) -> None:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))

    ollama = registry.provider("ollama")
    lmstudio = registry.provider("lmstudio")

    assert ollama.local is True
    assert ollama.configured is True
    assert ollama.base_url == "http://localhost:11434"
    assert lmstudio.local is True
    assert lmstudio.configured is True
    assert lmstudio.base_url == "http://localhost:1234/v1"


def test_provider_health_and_model_cache_are_persisted(tmp_path: Path) -> None:
    storage = ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore())
    registry = ProviderRegistry(storage)
    registry.configure_provider("openrouter", {"api_key": "health-test-key", "enabled": True})

    registry.save_health(
        "openrouter",
        {
            "provider_id": "openrouter",
            "ok": False,
            "status": "error",
            "message": "Invalid API key or insufficient credits",
            "checked_at": "2026-06-19T12:00:00Z",
        },
    )
    registry.save_models(
        "openrouter",
        registry.list_models("openrouter"),
    )

    provider = registry.provider("openrouter")

    assert provider.health_status == "error"
    assert provider.last_error == "Invalid API key or insufficient credits"
    assert provider.last_checked_at == "2026-06-19T12:00:00Z"
    assert provider.models_cached >= 1


def test_plaintext_key_is_migrated_out_of_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"providers":{"openai":{"api_key":"legacy-secret"}},"routes":{},"health":{},"models":{}}', encoding="utf-8")
    credentials = MemoryCredentialStore()
    storage = ProviderSettingsStorage(path, credential_store=credentials)

    assert storage.api_key("openai") == "legacy-secret"
    assert credentials.get("openai") == "legacy-secret"
    assert "legacy-secret" not in path.read_text(encoding="utf-8")


def test_corrupt_settings_return_defaults_with_warning_and_backup(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "settings.json"
    corrupt_content = '{"providers":{"openrouter":'
    path.write_text(corrupt_content, encoding="utf-8")
    storage = ProviderSettingsStorage(path, credential_store=MemoryCredentialStore())

    with caplog.at_level(logging.WARNING, logger="backend.model_router.storage"):
        result = storage.read()

    assert result == {"providers": {}, "routes": {}, "health": {}, "models": {}}
    assert storage.last_read_warning is not None
    assert storage.last_read_warning.startswith("model_router_settings_corrupt:JSONDecodeError:")
    assert storage.last_corrupt_backup is not None
    assert storage.last_corrupt_backup.name.startswith("settings.json.corrupt")
    assert storage.last_corrupt_backup.read_text(encoding="utf-8") == corrupt_content
    assert path.read_text(encoding="utf-8") == corrupt_content
    assert "Corrupt model-router settings detected" in caplog.text


def test_invalid_settings_root_is_reported_as_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[]", encoding="utf-8")
    storage = ProviderSettingsStorage(path, credential_store=MemoryCredentialStore())

    assert storage.read() == {"providers": {}, "routes": {}, "health": {}, "models": {}}
    assert storage.last_read_warning is not None
    assert "invalid_root" in storage.last_read_warning
    assert storage.last_corrupt_backup is not None


def test_atomic_replace_failure_preserves_original_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "settings.json"
    original = '{"providers":{"openrouter":{"enabled":true}}}\n'
    path.write_text(original, encoding="utf-8")
    storage = ProviderSettingsStorage(path, credential_store=MemoryCredentialStore())

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated replace failure")

    monkeypatch.setattr("backend.model_router.storage.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        storage.write({"providers": {}, "routes": {}, "health": {}, "models": {}})

    assert path.read_text(encoding="utf-8") == original
    assert not path.with_suffix(".json.tmp").exists()
