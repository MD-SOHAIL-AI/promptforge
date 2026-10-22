"""Reproduce the live-review findings without touching application state.

Run from the repository root. Fixtures and app state stay in this audit folder;
production SQLite databases are opened in read-only mode. No model calls occur.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUTPUT = Path(__file__).resolve().parent
FIXTURES = OUTPUT / "probe-fixtures"
FIXTURES.mkdir(parents=True, exist_ok=True)

for name, value in {
    "FORGEX_ROOT": FIXTURES / "app-state",
    "FORGEX_SETTINGS_PATH": FIXTURES / "settings.json",
    "FORGEX_MODEL_ROUTER_SETTINGS_PATH": FIXTURES / "models.json",
    "FORGEX_LIVE_LOG_PATH": FIXTURES / "live-runtime.jsonl",
}.items():
    os.environ[name] = str(value)
for name in ("FORGEX_DATA_DIR", "FORGEX_LEGACY_DATA_DIR", "PROMPTFORGE_ROOT"):
    os.environ.pop(name, None)

from backend.agent_runtime.failure_contract import failure_details
from backend.agent_runtime.jobs import TRANSIENT_PROVIDER_ERRORS
from backend.agent_runtime.task_contract import create_task_contract
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import ModelInfo
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router import storage
from backend.services.project_profile import inspect_project_profile

# App imports may construct a default application. It uses the isolated paths
# above and an in-memory credential store, never developer credentials.
storage.default_credential_store = lambda: MemoryCredentialStore()


def read_runs():
    database = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM agent_runs WHERE project_id=? ORDER BY created_at",
            ("external-esp32-room-monitor-e02997f653",),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def main():
    runs = read_runs()
    original = runs[0]["_recovery_context"]["active_workspace"]["task_contract"]
    contract = create_task_contract(
        original["request"], project_id="review-fixture", session_id="review-session",
        workspace=FIXTURES, intent="generate", prohibited=("flash", "hardware"),
    )
    # This is the observed defect, not an assertion of intended behavior.
    assert contract["requirements"] == []
    assert contract["exclusions"] == [original["request"]]
    assert not any(item["kind"] == "build" for item in contract["acceptance_checks"])

    quota_failure = failure_details("RATE_LIMITED", status="failed")
    assert quota_failure["category"] == "budget_exhaustion"
    assert quota_failure["origin"] == "forgex"
    assert "RATE_LIMITED" not in TRANSIENT_PROVIDER_ERRORS

    gone_failure = failure_details(
        "PROVIDER_REQUEST_FAILED", status="failed", actions=("retry",),
        evidence={"diagnostics": {"http_status": 410, "last_failure_origin": "opencode"}},
    )
    assert gone_failure["category"] == "runtime"
    assert gone_failure["normalized_code"] == "FORGEX_RUNTIME_ERROR"

    blank = FIXTURES / "blank-platformio"
    blank.mkdir(exist_ok=True)
    (blank / "platformio.ini").write_text("", encoding="utf-8")
    try:
        inspect_project_profile(blank)
    except ValueError as exc:
        blank_error = {"type": type(exc).__name__, "message": str(exc)}
    else:
        raise AssertionError("Empty platformio.ini did not reproduce the exception")

    from tests.api.test_api_routes import make_client
    client, project_id, _ = make_client(FIXTURES / "http-fixture")
    with client:
        project = client.get(f"/projects/{project_id}").json()
        (Path(project["project_path"]) / "platformio.ini").write_text("", encoding="utf-8")
        readiness = client.get(f"/projects/{project_id}/readiness")
        profile = client.get(f"/projects/{project_id}/profile")
        assert readiness.status_code == 500
        assert profile.status_code == 422

    class CachedRegistry:
        def list_models(self, provider_id):
            return [ModelInfo(provider_id=provider_id,
                model_id="qwen/qwen3-coder-480b-a35b-instruct",
                display_name="Retired cached model")]

    # The production OpenCode adapter returns the cache without discovery.
    advertised = asyncio.run(OpenCodeProvider(CachedRegistry(), "nvidia_nim").list_models())
    assert advertised[0].model_id == "qwen/qwen3-coder-480b-a35b-instruct"

    stage = ROOT / ".promptforge/state/change-staging/workflow-agent-run-91875dbc199d4b39a69c231765f78ab1"
    missing_headers = [name for name in (
        "sensor_handler.h", "display_handler.h", "web_server.h",
        "alert_handler.h", "button_handler.h", "demo_mode.h",
    ) if not list(stage.rglob(name))]
    assert len(missing_headers) == 6
    assert all(not run.get("build_result") for run in runs)

    result = {
        "task_partition": {
            "request_characters": len(original["request"]),
            "positive_requirements": len(contract["requirements"]),
            "whole_request_is_excluded": True,
            "checks": [item["kind"] for item in contract["acceptance_checks"]],
            "deliverables": contract["deliverables"],
        },
        "rate_limit_classification": {
            key: quota_failure[key] for key in ("category", "origin", "normalized_code", "retryable")
        },
        "rate_limit_supported_by_job_recovery": "RATE_LIMITED" in TRANSIENT_PROVIDER_ERRORS,
        "retired_model_classification": {
            key: gone_failure[key] for key in ("category", "origin", "normalized_code", "recovery_actions")
        },
        "blank_platformio": {
            "exception": blank_error,
            "readiness_http_status": readiness.status_code,
            "profile_http_status": profile.status_code,
        },
        "cached_model_returned_by_active_adapter": advertised[0].model_id,
        "saved_draft": {
            "files": sorted(path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()),
            "missing_headers": missing_headers,
            "build_result_present": False,
        },
    }
    (OUTPUT / "probe-results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
