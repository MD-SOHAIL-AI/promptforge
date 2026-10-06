from scripts.agent_qualification import wait_for_job, recovery_metrics


def test_qualification_follows_scheduled_recovery_to_replacement():
    job = {"current_run_id": "r1", "status": "paused_model_unavailable", "recovery_policy": "safe_auto", "retry_due_at": "later"}
    first = {"run_id": "r1", "job_id": "j", "status": "paused_model_unavailable"}
    replacement = {"run_id": "r2", "job_id": "j", "status": "completed"}
    def request(route):
        if "/jobs/" in route:
            return {"job": job}
        return {"run": first if route.endswith("r1") else replacement}
    def sleep(seconds):
        job.update(current_run_id="r2", status="completed", retry_due_at=None)
    settled, attempts = wait_for_job(request, first, 100, clock=lambda: 0, sleep=sleep)
    assert settled == replacement
    assert [run["run_id"] for run in attempts] == ["r1", "r2"]


def test_qualification_does_not_resume_a_budget_paused_job():
    run = {"run_id": "r", "job_id": "j", "status": "failed"}
    job = {"current_run_id": "r", "status": "paused", "paused_by": "budget", "recovery_policy": "safe_auto"}
    result, attempts = wait_for_job(lambda route: {"job": job}, run, 100, sleep=lambda _: (_ for _ in ()).throw(AssertionError("must not wait")))
    assert result == run and len(attempts) == 1


def test_recovered_success_includes_request_retries_within_one_run():
    run = {"run_id": "r", "status": "completed", "provider_diagnostics": {"physical_request_count": 3},
           "events": [{"event_type": "model.retry.started"}, {"event_type": "model.retry.started"}]}
    metrics = recovery_metrics([run], 9)
    assert metrics["recovered"] is True
    assert metrics["request_retries"] == 2
    assert metrics["physical_requests"] == 3
    assert metrics["replacement_runs"] == 0
    failed = recovery_metrics([run, {"run_id": "r2", "status": "failed"}], 12)
    assert failed["recovered"] is False
    assert failed["replacement_runs"] == 1
