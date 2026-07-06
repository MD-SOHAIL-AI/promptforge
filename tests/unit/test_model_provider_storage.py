from __future__ import annotations

from pathlib import Path

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
