"""Test-process defaults isolated from developer-local dotenv overrides."""

from __future__ import annotations

import os
from pathlib import Path

from backend.core.config import DEFAULT_LLM_TIMEOUT_SECONDS, ENV_FILE_ENV_VAR, LLM_TIMEOUT_ENV_VAR


# Application startup intentionally reads the repository .env file. Tests use
# the documented default explicitly so a developer's local override cannot
# make otherwise deterministic configuration assertions fail.
os.environ[LLM_TIMEOUT_ENV_VAR] = str(int(DEFAULT_LLM_TIMEOUT_SECONDS))
# Test-created applications must not inherit feature flags or credentials from
# a developer's repository-local .env file.
os.environ[ENV_FILE_ENV_VAR] = str(Path(__file__).with_name(".env.test"))
