"""Normalize adapter-specific failures into stable ForgeX error codes."""

from __future__ import annotations

from .contracts import ProviderErrorCode


def normalize_provider_error(value: str | None) -> ProviderErrorCode:
    text = (value or "").casefold()
    if "key_missing" in text or "missing_api_key" in text:
        return ProviderErrorCode.MISSING_API_KEY
    if "auth_invalid" in text or "invalid_api_key" in text:
        return ProviderErrorCode.INVALID_API_KEY
    if "rate" in text and "limit" in text:
        return ProviderErrorCode.PROVIDER_RATE_LIMITED
    if "quota" in text or "usage_limit" in text:
        return ProviderErrorCode.CLI_USAGE_LIMIT_REACHED if "codex" in text or "cli" in text else ProviderErrorCode.PROVIDER_RATE_LIMITED
    if "not_logged" in text or "auth_required" in text or "login_required" in text:
        return ProviderErrorCode.CLI_NOT_LOGGED_IN
    if "not_found" in text and ("cli" in text or "codex" in text):
        return ProviderErrorCode.CLI_NOT_FOUND
    if "permission" in text or "policy_denied" in text:
        return ProviderErrorCode.CLI_PERMISSION_BLOCKED
    if "workspace" in text and ("trust" in text or "escape" in text):
        return ProviderErrorCode.WORKSPACE_NOT_TRUSTED
    if "response" in text or "schema" in text or "json" in text:
        return ProviderErrorCode.PROVIDER_INVALID_RESPONSE
    if "content" in text or "path_unsafe" in text or "output" in text:
        return ProviderErrorCode.INVALID_GENERATED_OUTPUT
    return ProviderErrorCode.PROVIDER_UNAVAILABLE


def actionable_message(code: ProviderErrorCode, provider_name: str) -> str:
    messages = {
        ProviderErrorCode.MISSING_API_KEY: f"{provider_name} has no API key configured. Add one in Provider Settings or choose another provider.",
        ProviderErrorCode.INVALID_API_KEY: f"{provider_name} rejected the configured API key. Replace it in Provider Settings.",
        ProviderErrorCode.CLI_NOT_FOUND: f"{provider_name} is not installed or is not available on PATH.",
        ProviderErrorCode.CLI_NOT_LOGGED_IN: f"{provider_name} is installed but not logged in. Run the official CLI login command, then retry.",
        ProviderErrorCode.CLI_USAGE_LIMIT_REACHED: f"{provider_name} usage limit was reached. Retry when usage is available.",
        ProviderErrorCode.CLI_PERMISSION_BLOCKED: f"{provider_name} could not run inside the managed ForgeX workspace.",
        ProviderErrorCode.PROVIDER_RATE_LIMITED: f"{provider_name} is rate limited. Retry later or use the configured fallback.",
        ProviderErrorCode.INVALID_GENERATED_OUTPUT: f"{provider_name} returned output that ForgeX rejected during validation.",
    }
    return messages.get(code, f"{provider_name} is currently unavailable.")


def classify_cli_failure(text: str, *, returncode: int | None = None) -> ProviderErrorCode:
    value = " ".join(text.casefold().split())[:4096]
    if any(marker in value for marker in ("not logged in", "login required", "authentication required", "unauthorized")):
        return ProviderErrorCode.CLI_NOT_LOGGED_IN
    if any(marker in value for marker in ("usage limit", "quota exceeded", "limit reached", "insufficient quota")):
        return ProviderErrorCode.CLI_USAGE_LIMIT_REACHED
    if any(marker in value for marker in ("permission denied", "workspace is not trusted", "sandbox denied", "operation not permitted")):
        return ProviderErrorCode.CLI_PERMISSION_BLOCKED
    if returncode is not None and returncode < 0:
        return ProviderErrorCode.PROVIDER_UNAVAILABLE
    return ProviderErrorCode.PROVIDER_UNAVAILABLE
