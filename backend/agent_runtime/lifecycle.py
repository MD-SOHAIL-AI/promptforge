"""Shared lifecycle rules used by both runtime adapters."""
from __future__ import annotations
import inspect
from backend.contracts.runtime_policy import AGENT_TURN_SECONDS, GENERATION_TURN_SECONDS, REVIEW_UNIT_SECONDS


def bind_job_resources(planner, jobs, job, run_id):
    binder = getattr(planner, "bind_job_budget", None)
    if not callable(binder):
        return
    kwargs = {"reserve": lambda charges: jobs.reserve_provider_attempt(job["job_id"], run_id, charges)}
    if "settle" in inspect.signature(binder).parameters:
        kwargs["settle"] = lambda attempt_id, actual: jobs.settle_provider_attempt(job["job_id"], attempt_id, actual)
    binder(job, lambda used: jobs.persist_provider_budget(job["job_id"], used), **kwargs)
    review_binder = getattr(planner, "bind_review_cache", None)
    if callable(review_binder):
        review_binder(lambda key: jobs.review_units(job["job_id"], key),
                     lambda key, unit: jobs.save_review_unit(job["job_id"], key, unit))


def turn_allowance(*, phase, remaining, turn_timeout, verification_reserve, bounded_job):
    """Generation leaves time for a repair turn and host verification."""
    cap = min(turn_timeout, REVIEW_UNIT_SECONDS if phase == "review" else
              AGENT_TURN_SECONDS if phase != "generation" else GENERATION_TURN_SECONDS)
    if not bounded_job:
        return cap
    return min(cap, remaining - verification_reserve)
