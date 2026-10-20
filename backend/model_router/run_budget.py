"""A shared, conservative physical-request budget for parent and child agents."""
import threading
import json

_LOCK = threading.RLock()
LIMITS = {"requests": 120, "output_tokens": 245_760, "input_bytes": 8_000_000}


class AgentBudgetExceeded(ValueError):
    def __init__(self, dimension, consumed, limit, attempted_charge, *, code="AGENT_PROVIDER_BUDGET_EXCEEDED"):
        super().__init__(code)
        self.details = {"dimension": dimension, "consumed": consumed,
                        "limit": limit, "attempted_charge": attempted_charge,
                        "origin": "job_budget"}
        self.code = code


class AgentBudgetPersistenceError(RuntimeError):
    def __init__(self, cause=None):
        super().__init__("AGENT_BUDGET_PERSISTENCE_FAILED")
        allowed = {"AGENT_BUDGET_CHARGE_INVALID", "AGENT_ATTEMPT_CONFLICT", "AGENT_JOB_NOT_RUNNING",
                   "AGENT_INTAKE_NOT_RUNNING", "AGENT_INTAKE_NOT_FOUND", "AGENT_ATTEMPT_USAGE_INVALID"}
        self.details = {"origin": "job_budget_persistence",
                        "cause_code": str(cause) if str(cause) in allowed else "AGENT_BUDGET_STORAGE_FAILED"}


def validate_attempt_charges(charges):
    """Separate physical-attempt identity from strictly numeric budget dimensions."""
    if (not isinstance(charges, dict) or set(charges) - {*LIMITS, "_attempt_id"}
            or not set(LIMITS).issubset(charges)
            or any(type(charges[key]) is not int or charges[key] < 0 for key in LIMITS)):
        raise ValueError("AGENT_BUDGET_CHARGE_INVALID")
    attempt_id = charges.get("_attempt_id")
    if attempt_id is not None and (not isinstance(attempt_id, str) or not 1 <= len(attempt_id) <= 128):
        raise ValueError("AGENT_BUDGET_CHARGE_INVALID")
    return {key: charges[key] for key in LIMITS}, attempt_id


def new_budget(saved=None):
    value = saved if isinstance(saved, dict) else {}
    result = {key: max(0, int(value.get(key, 0))) for key in LIMITS}
    if isinstance(value.get("_limits"), dict):
        result["_limits"] = {key:max(1,min(int(value["_limits"].get(key,limit)),limit*10)) for key,limit in LIMITS.items()}
    return result


def charge_attempt(request, *, attempt_id=None):
    from .models import execution_value
    budget = execution_value(request, "budget", "forgex_run_budget")
    if not isinstance(budget, dict):
        return
    # Reserve maximum output before sending. Failures and repair requests cost
    # budget too; token accounting never refunds an uncertain provider result.
    messages = request.metadata.get("forgex_messages")
    context = (json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
               if isinstance(messages, (list, tuple)) and messages else request.prompt)
    context += request.system_prompt or ""
    for key in ("forgex_native_tools", "forgex_structured_output"):
        value = request.metadata.get(key)
        if value:
            context += json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    charges = {"requests": 1, "output_tokens": int(request.max_tokens or 4096),
               "input_bytes": len(context.encode("utf-8"))}
    diagnostics = execution_value(request, "diagnostics", "forgex_attempt_diagnostics", {})
    if attempt_id or diagnostics.get("physical_attempt_id"):
        charges["_attempt_id"] = attempt_id or diagnostics["physical_attempt_id"]
    with _LOCK:
        limits = budget.get("_limits", LIMITS)
        for key in LIMITS:
            consumed = int(budget.get(key, 0))
            if consumed + charges[key] > limits[key]:
                raise AgentBudgetExceeded(key, consumed, limits[key], charges[key])
        if request.metadata.get("agent_phase") == "generation" and budget.get("_phase_reserve"):
            for key, reserve in budget["_phase_reserve"].items():
                consumed = int(budget.get(key, 0))
                if consumed + charges[key] > limits[key] - reserve:
                    error = AgentBudgetExceeded(key, consumed, limits[key], charges[key], code="AGENT_VERIFICATION_RESERVE_REACHED")
                    error.details["origin"] = "verification_reserve"
                    raise error
        persist = budget.get("_persist")
        reserve = budget.get("_reserve")
        if callable(reserve):
            try:
                reserved = reserve(charges)
            except AgentBudgetExceeded:
                raise
            except Exception as exc:
                raise AgentBudgetPersistenceError(exc) from exc
            for key in LIMITS:
                budget[key] = reserved[key]
            return
        if callable(persist):
            try:
                persist({key:int(budget.get(key,0))+charges[key] for key in LIMITS})
            except AgentBudgetExceeded:
                raise
            except Exception as exc:
                raise AgentBudgetPersistenceError(exc) from exc
        for key in LIMITS:
            charge = charges[key]
            budget[key] = int(budget.get(key, 0)) + charge


def settle_attempt(request, actual_output, *, complete):
    from .models import execution_value
    if not complete or type(actual_output) is not int or actual_output < 0:
        return
    budget = execution_value(request, "budget", "forgex_run_budget", {})
    diagnostics = execution_value(request, "diagnostics", "forgex_attempt_diagnostics", {})
    settle = budget.get("_settle")
    if callable(settle) and diagnostics.get("physical_attempt_id"):
        with _LOCK:
            try:
                consumed = settle(diagnostics["physical_attempt_id"], actual_output)
            except Exception as exc:
                raise AgentBudgetPersistenceError(exc) from exc
            for key in LIMITS:
                budget[key] = consumed[key]


def snapshot(budget):
    with _LOCK:
        return {key:value for key,value in budget.items() if not key.startswith("_") or key == "_limits"}
