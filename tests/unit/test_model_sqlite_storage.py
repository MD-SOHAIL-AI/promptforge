import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.sqlite_storage import SQLiteProviderSettingsStorage
from backend.model_router.storage import ModelRouterStorageError


def test_migration_preserves_legacy_and_does_not_copy_secrets(tmp_path):
    legacy = tmp_path / "legacy.json"
    payload = json.dumps({"providers": {"openai": {"enabled": True, "api_key": "private-key"}}, "routes": {"planning": {"model_id": "chosen"}}})
    legacy.write_text(payload, encoding="utf-8")
    credentials = MemoryCredentialStore()
    store = SQLiteProviderSettingsStorage(tmp_path / "models.sqlite3", legacy_json_path=legacy, credential_store=credentials)
    assert store.api_key("openai") == "private-key"
    assert "api_key" not in store.provider_settings("openai")
    assert store.routes()["planning"]["model_id"] == "chosen"
    assert legacy.read_text(encoding="utf-8") == payload
    store.save_route("planning", {"model_id": "new"})
    restored = SQLiteProviderSettingsStorage(store.path, legacy_json_path=legacy, credential_store=credentials)
    assert restored.routes()["planning"]["model_id"] == "new"


def test_independent_storage_instances_do_not_lose_updates(tmp_path):
    path = tmp_path / "models.sqlite3"
    stores = [SQLiteProviderSettingsStorage(path, credential_store=MemoryCredentialStore()) for _ in range(4)]
    def save(index):
        stores[index % 4].save_route(f"task-{index}", {"model_id": f"model-{index}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(40)))
    assert len(stores[0].routes()) == 40


def test_corrupt_migration_can_be_retried_after_source_repair(tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text("{bad", encoding="utf-8")
    path = tmp_path / "models.sqlite3"
    with pytest.raises(ModelRouterStorageError):
        SQLiteProviderSettingsStorage(path, legacy_json_path=legacy)
    assert legacy.read_text() == "{bad"
    legacy.write_text('{"routes": {"planning": {"model_id": "fixed"}}}', encoding="utf-8")
    store = SQLiteProviderSettingsStorage(path, legacy_json_path=legacy)
    assert store.routes()["planning"]["model_id"] == "fixed"


def test_usage_import_is_once_only_bounded_and_preserves_source(tmp_path):
    from backend.model_router.usage import SQLiteUsageTracker
    source = tmp_path / "usage.jsonl"
    original = '\n'.join([json.dumps({"model_id": str(i)}) for i in range(5)]) + '\n{broken\n[]\n'
    source.write_text(original, encoding="utf-8")
    path = tmp_path / "models.sqlite3"
    first = SQLiteUsageTracker(path, legacy_jsonl_path=source)
    second = SQLiteUsageTracker(path, legacy_jsonl_path=source)
    assert len(first.list_records()) == len(second.list_records()) == 5
    assert second.list_records(2) == [{"model_id": "3"}, {"model_id": "4"}]
    assert second.list_records(0) == []
    assert source.read_text(encoding="utf-8") == original


def test_invalid_sqlite_sections_do_not_replace_good_state(tmp_path):
    store = SQLiteProviderSettingsStorage(tmp_path / "models.sqlite3")
    store.save_route("planning", {"model_id": "kept"})
    with pytest.raises(ModelRouterStorageError, match="invalid_section"):
        store.write({"routes": []})
    assert store.routes()["planning"]["model_id"] == "kept"
