"""Test-process defaults isolated from developer-local dotenv overrides."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from backend.core.config import DEFAULT_LLM_TIMEOUT_SECONDS, LLM_TIMEOUT_ENV_VAR


# Application startup intentionally reads the repository .env file. Tests use
# the documented default explicitly so a developer's local override cannot
# make otherwise deterministic configuration assertions fail.
os.environ[LLM_TIMEOUT_ENV_VAR] = str(int(DEFAULT_LLM_TIMEOUT_SECONDS))

# Protect import-time application construction as well as individual tests.
_session_storage = tempfile.TemporaryDirectory(prefix="forgex-pytest-")
os.environ["FORGEX_ROOT"] = _session_storage.name
os.environ["FORGEX_SETTINGS_PATH"] = str(Path(_session_storage.name) / "settings.json")
os.environ["FORGEX_MODEL_ROUTER_SETTINGS_PATH"] = str(Path(_session_storage.name) / "model-router" / "settings.json")
os.environ["FORGEX_LIVE_LOG_PATH"] = str(Path(_session_storage.name) / "live-runtime.jsonl")
for _variable in ("FORGEX_DATA_DIR", "FORGEX_LEGACY_DATA_DIR", "PROMPTFORGE_ROOT"):
    os.environ.pop(_variable, None)


@pytest.fixture(autouse=True)
def isolated_application_storage(tmp_path, monkeypatch):
    """Default app construction cannot reconcile user runs or read API keys."""
    from backend.model_router import storage
    from backend.model_router.credentials import MemoryCredentialStore
    monkeypatch.setenv("FORGEX_ROOT", str(tmp_path / "app-default"))
    monkeypatch.setenv("FORGEX_SETTINGS_PATH", str(tmp_path / "settings-default.json"))
    monkeypatch.setenv("FORGEX_MODEL_ROUTER_SETTINGS_PATH", str(tmp_path / "model-router-default" / "settings.json"))
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(tmp_path / "live-runtime.jsonl"))
    for variable in ("FORGEX_DATA_DIR", "FORGEX_LEGACY_DATA_DIR", "PROMPTFORGE_ROOT"):
        monkeypatch.delenv(variable, raising=False)
    credentials = MemoryCredentialStore()
    monkeypatch.setattr(storage, "default_credential_store", lambda: credentials)
