"""Additive failure information; never turn unknown errors into outages."""
from .run_reporting import normalize_provider_error


def failure_details(code, *, status, actions=(), evidence=None):
    if status not in {"failed", "blocked", "timed_out", "cancelled", "paused_model_unavailable"}:
        return None
    original = str(code or "FORGEX_INTERNAL_ERROR")
    normalized = normalize_provider_error(original).value
    lowered = original.casefold()
    if "cancel" in lowered:
        category = "cancellation"
    elif "interrupt" in lowered:
        category = "interruption"
    elif any(word in lowered for word in ("no_progress", "repeated_call")):
        category = "stalled_progress"
    elif "budget_persistence" in lowered:
        category = "runtime"
    elif original == "AGENT_TURN_LIMIT_EXCEEDED":
        category = "execution_deadline"
    elif any(word in lowered for word in ("budget", "max_turn", "limit")):
        category = "budget_exhaustion"
    elif any(word in lowered for word in ("policy", "unsafe", "quarantin", "trust", "capability")):
        category = "policy_rejection"
    elif "dependency" in lowered or "package" in lowered:
        category = "dependency_resolution"
    elif "build" in lowered or "compil" in lowered:
        category = "compilation"
    elif normalized == "PROVIDER_BUSY":
        category = "local_model_queue"
    elif normalized in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT", "PROVIDER_RATE_LIMITED", "PROVIDER_HTTP_ERROR", "PROVIDER_AUTH_ERROR", "MISSING_API_KEY", "INVALID_API_KEY"}:
        category = "provider_transport"
    elif any(word in normalized for word in ("OUTPUT", "RESPONSE", "TOOLPLAN", "ARGUMENT")):
        category = "invalid_model_output"
    else:
        category = "runtime"
    automatic_retry = original.upper() in {"API_RATE_LIMITED", "PROVIDER_RATE_LIMITED", "API_NETWORK_ERROR",
        "PROVIDER_TIMEOUT", "PROVIDER_BUSY", "PROVIDER_UNAVAILABLE"}
    if status in {"blocked", "cancelled"}:
        automatic_retry = False
    diagnostics = (evidence or {}).get("diagnostics") or {}
    if category in {"execution_deadline", "budget_exhaustion"}:
        normalized = original
    return {"schema_version": 2, "category": category, "original_code": original,
            "primary_code": diagnostics.get("initial_failure_code") or original,
            "recovery_code": diagnostics.get("recovery_failure_code"),
            "terminal_stop_code": original,
            "normalized_code": normalized, "origin": "provider" if category == "provider_transport" else "forgex",
            "retryable": automatic_retry, "recovery_actions": list(actions),
            "timeout_stage": diagnostics.get("timeout_stage"),
            "physical_attempt_id": diagnostics.get("physical_attempt_id"),
            "cause_origin": diagnostics.get("last_failure_origin"),
            "evidence": evidence or {}}
