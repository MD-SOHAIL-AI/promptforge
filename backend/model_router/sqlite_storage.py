"""Transactional model settings using the existing registry storage interface."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from .storage import ProviderSettingsStorage, ModelRouterStorageError, _empty_state


class _TransactionLock:
    """Hold the SQLite transaction across inherited read/modify/write methods."""
    def __init__(self, store):
        self.store = store
        self.mutex = threading.RLock()
        self.depth = 0
        self.failed = False

    def __enter__(self):
        self.mutex.acquire()
        try:
            if self.depth == 0:
                self.store._connection = self.store._connect()
                try:
                    self.store._connection.execute("BEGIN IMMEDIATE")
                except BaseException:
                    self.store._connection.close()
                    self.store._connection = None
                    raise
                self.failed = False
            self.depth += 1
            return self
        except BaseException:
            self.mutex.release()
            raise

    def __exit__(self, kind, value, traceback):
        self.failed = self.failed or kind is not None
        self.depth -= 1
        try:
            if self.depth == 0:
                connection = self.store._connection
                try:
                    connection.rollback() if self.failed else connection.commit()
                finally:
                    connection.close()
                    self.store._connection = None
        finally:
            self.mutex.release()


class SQLiteProviderSettingsStorage(ProviderSettingsStorage):
    def __init__(self, path: Path, *, legacy_json_path: Path | None = None, credential_store=None):
        super().__init__(path, credential_store=credential_store)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = None
        self._lock = _TransactionLock(self)
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""CREATE TABLE IF NOT EXISTS model_settings(
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                payload TEXT NOT NULL)""")
        with self._lock:
            if self._connection.execute("SELECT 1 FROM model_settings WHERE id=1").fetchone():
                return
            state = _empty_state()
            if legacy_json_path is not None and legacy_json_path.exists():
                legacy = ProviderSettingsStorage(legacy_json_path, credential_store=self.credential_store)
                state = legacy.read()  # Refuse corrupt input; never replace it with defaults.
                for provider_id, settings in state["providers"].items():
                    if not isinstance(settings, dict):
                        continue
                    secret = settings.pop("api_key", None)
                    if isinstance(secret, str) and secret.strip():
                        self.credential_store.set(provider_id, secret.strip())
                        settings["credential_ref"] = provider_id
            self._write_unlocked(state)

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def _read_unlocked(self, *, fresh: bool = False):
        snapshot = self._snapshot.get()
        if not fresh and snapshot is not None:
            return snapshot
        row = self._connection.execute("SELECT version,payload FROM model_settings WHERE id=1").fetchone()
        if row is None:
            return _empty_state()
        if row[0] != 1:
            raise ModelRouterStorageError("model_settings_schema_unsupported")
        try:
            state = json.loads(row[1])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ModelRouterStorageError("model_router_settings_invalid_json") from exc
        if not isinstance(state, dict):
            raise ModelRouterStorageError("model_router_settings_invalid_root")
        for key in _empty_state():
            if not isinstance(state.get(key, {}), dict):
                raise ModelRouterStorageError("model_router_settings_invalid_section")
        return {key: state.get(key, {}) for key in _empty_state()}

    def _write_unlocked(self, data):
        state = {key: data.get(key, {}) for key in _empty_state()}
        if any(not isinstance(section, dict) for section in state.values()):
            raise ModelRouterStorageError("model_router_settings_invalid_section")
        for settings in state["providers"].values():
            if isinstance(settings, dict) and "api_key" in settings:
                raise ModelRouterStorageError("plaintext_credentials_forbidden")
        self._connection.execute(
            "INSERT INTO model_settings VALUES(1,1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (json.dumps(state, ensure_ascii=True, sort_keys=True),),
        )
        if self._snapshot.get() is not None:
            self._snapshot.set(state)
