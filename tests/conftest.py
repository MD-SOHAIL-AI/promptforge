"""Test-process defaults isolated from developer-local dotenv overrides."""

from __future__ import annotations

import os

from backend.core.config import DEFAULT_LLM_TIMEOUT_SECONDS, LLM_TIMEOUT_ENV_VAR


# Application startup intentionally reads the repository .env file. Tests use
# the documented default explicitly so a developer's local override cannot
# make otherwise deterministic configuration assertions fail.
os.environ[LLM_TIMEOUT_ENV_VAR] = str(int(DEFAULT_LLM_TIMEOUT_SECONDS))
