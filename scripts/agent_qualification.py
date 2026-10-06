"""Follow a durable objective through scheduled retries and replacement runs."""
import time

TERMINAL = {"completed", "completed_with_warning", "failed", "blocked", "cancelled", "timed_out", "paused_model_unavailable"}


def wait_for_job(request, run, deadline, *, clock=time.monotonic, sleep=time.sleep):
    attempts = {}
    while True:
        attempts[run["run_id"]] = run
        job = request("/agent-runtime/jobs/" + run["job_id"])["job"] if run.get("job_id") else run.get("job")
        if job and job["current_run_id"] != run["run_id"]:
            run = request("/agent-runtime/runs/" + job["current_run_id"])["run"]
            continue
        recovering = bool(job and job.get("recovery_policy") == "safe_auto"
            and not job.get("paused_by") and not job.get("blocking_reason")
            and job.get("auto_recovery_count", 0) < 3
            and (job.get("retry_due_at") or job.get("status") == "interrupted"))
        if run["status"] in TERMINAL and not recovering:
            return run, list(attempts.values())
        if clock() > deadline:
            raise TimeoutError("QUALIFICATION_JOB_DID_NOT_SETTLE")
        sleep(1)
        run = request("/agent-runtime/runs/" + run["run_id"])["run"]


def recovery_metrics(attempts, elapsed):
    physical = sum((run.get("provider_diagnostics") or {}).get("physical_request_count", 0) for run in attempts)
    retries = sum(event.get("event_type") == "model.retry.started" for run in attempts for event in run.get("events", ()))
    succeeded = attempts[-1].get("status") in {"completed", "completed_with_warning"}
    return {"run_ids": [run["run_id"] for run in attempts], "recovered": succeeded and (len(attempts) > 1 or retries > 0),
            "request_retries": retries, "replacement_runs": max(0, len(attempts) - 1),
            "physical_requests": physical, "elapsed_seconds": elapsed,
            "immediate_internal_failure": len(attempts) == 1 and elapsed <= 5 and
                (attempts[-1].get("failure") or {}).get("category") == "runtime"}
