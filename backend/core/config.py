"""Environment loading for PromptForge application configuration."""

from __future__ import annotations

import math
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LLM_TIMEOUT_ENV_VAR = "PROMPTFORGE_LLM_TIMEOUT_SECONDS"
DEFAULT_LLM_TIMEOUT_SECONDS = 180.0
LLM_MAX_ATTEMPTS_ENV_VAR = "PROMPTFORGE_LLM_MAX_ATTEMPTS"
DEFAULT_LLM_MAX_ATTEMPTS = 2
LLM_RETRY_BACKOFF_ENV_VAR = "PROMPTFORGE_LLM_RETRY_BACKOFF_SECONDS"
DEFAULT_LLM_RETRY_BACKOFF_SECONDS = 1.0


def load_environment(env_file: str | Path | None = None) -> Path:
    """Load PromptForge's dotenv file without overriding process variables."""

    path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path


def llm_timeout_seconds() -> float:
    """Return the validated application-wide LLM timeout."""

    raw_value = os.getenv(LLM_TIMEOUT_ENV_VAR)
    if raw_value is None or not raw_value.strip():
        return DEFAULT_LLM_TIMEOUT_SECONDS
    try:
        timeout_s = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{LLM_TIMEOUT_ENV_VAR} must be a positive finite number"
        ) from exc
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError(
            f"{LLM_TIMEOUT_ENV_VAR} must be a positive finite number"
        )
    return timeout_s


def llm_max_attempts() -> int:
    """Return the bounded number of attempts for retryable LLM failures."""

    raw_value = os.getenv(LLM_MAX_ATTEMPTS_ENV_VAR)
    if raw_value is None or not raw_value.strip():
        return DEFAULT_LLM_MAX_ATTEMPTS
    try:
        attempts = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{LLM_MAX_ATTEMPTS_ENV_VAR} must be an integer between 1 and 5"
        ) from exc
    if not 1 <= attempts <= 5:
        raise ValueError(
            f"{LLM_MAX_ATTEMPTS_ENV_VAR} must be an integer between 1 and 5"
        )
    return attempts


def llm_retry_backoff_seconds() -> float:
    """Return the initial delay used between retryable LLM attempts."""

    raw_value = os.getenv(LLM_RETRY_BACKOFF_ENV_VAR)
    if raw_value is None or not raw_value.strip():
        return DEFAULT_LLM_RETRY_BACKOFF_SECONDS
    try:
        backoff_s = float(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{LLM_RETRY_BACKOFF_ENV_VAR} must be a finite number from 0 to 60"
        ) from exc
    if not math.isfinite(backoff_s) or not 0 <= backoff_s <= 60:
        raise ValueError(
            f"{LLM_RETRY_BACKOFF_ENV_VAR} must be a finite number from 0 to 60"
        )
    return backoff_s
