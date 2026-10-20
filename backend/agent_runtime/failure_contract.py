"""Additive failure information; never turn unknown errors into outages."""
from .run_reporting import normalize_provider_error


def failure_details(code, *, status, actions=(), evidence=None):
    if status not in {"failed", "blocked", "timed_out", "cancelled", "paused_model_unavailable"}:
        return None
    original = str(code or "FORGEX_INTERNAL_ERROR")
    normalized = normalize_provider_error(original).value
    lowered = original.casefold()
    diagnostics = (evidence or {}).get("diagnostics") or {}
    if original == "REQUIREMENTS_UNVERIFIED":
        category = "requirement_review" if (evidence or {}).get("references_valid") else "requirement_evidence"
    elif original == "AGENT_REVIEW_REQUIRED":
        category = "invalid_model_output" if diagnostics.get("last_failure_origin") == "semantic_review" else "requirement_review"
    elif "cancel" in lowered:
        category = "cancellation"
    elif "interrupt" in lowered:
        category = "interruption"
    elif any(word in lowered for word in ("no_progress", "repeated_call")):
        category = "stalled_progress"
    elif "budget_persistence" in lowered:
        category = "runtime"
    elif original in {"AGENT_TURN_LIMIT_EXCEEDED", "AGENT_VERIFICATION_RESERVE_REACHED", "BUILD_PHASE_TIMEOUT", "DEPENDENCY_PHASE_TIMEOUT"}:
        category = "execution_deadline"
    elif any(word in lowered for word in ("budget", "max_turn", "limit")):
        category = "budget_exhaustion"
    elif any(word in lowered for word in ("policy", "unsafe", "quarantin", "trust", "capability")):
        category = "policy_rejection"
    elif original in {"BUILD_CONFIGURATION_MISSING", "BUILD_CONFIGURATION_INVALID", "BUILD_CONFIGURATION_REPAIR_REQUIRED"}:
        category = "project_configuration"
    elif "dependency" in lowered or "package" in lowered:
        category = "dependency_resolution"
    elif "build" in lowered or "compil" in lowered:
        category = "compilation"
    elif normalized == "PROVIDER_BUSY":
        category = "local_model_queue"
    elif original == "MODEL_RETIRED":
        category = "model_unavailable"
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
    if category in {"execution_deadline", "budget_exhaustion", "project_configuration"} or original == "MODEL_WORKER_FAILED":
        normalized = original
    return {"schema_version": 2, "category": category, "original_code": original,
            "primary_code": diagnostics.get("initial_failure_code") or original,
            "recovery_code": diagnostics.get("recovery_failure_code"),
            "terminal_stop_code": original,
            "normalized_code": original if original == "MODEL_RETIRED" else normalized, "origin": "provider" if category in {"provider_transport", "model_unavailable"} else "forgex",
            "retryable": automatic_retry, "recovery_actions": (
                ["inspect", "trust_project", "verify_locally"]
                if original == "AGENT_PROJECT_TRUST_REQUIRED" else list(actions)),
            "timeout_stage": diagnostics.get("timeout_stage"),
            "physical_attempt_id": diagnostics.get("physical_attempt_id"),
            "cause_origin": diagnostics.get("last_failure_origin"),
            "evidence": evidence or {}}
