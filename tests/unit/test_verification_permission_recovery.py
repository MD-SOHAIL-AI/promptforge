"""Permission-blocked verification must preserve drafts and remain resumable."""
import asyncio

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.tool_contracts import ProductToolPlan
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_tool_runtime import SequencePlanner, plan


@pytest.mark.parametrize("explicit_build", [False, True])
def test_generation_waits_for_permission_without_claiming_a_compiler_failure(tmp_path, explicit_build):
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    build_plan = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Build staged source",
        "final": False, "tool_calls": [{"id": "build", "type": "build_firmware", "arguments": {}}]})
    planner = SequencePlanner([plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n"),
        build_plan if explicit_build else plan(final=True)])
    builder = Builder()
    events = []
    async def publish(event, payload):
        events.append((event, payload))
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, planner=planner,
        progress=publish, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=False)
    result = asyncio.run(loop.run(task="Update and build the ESP32 project", require_build=True))
    assert result.classification == "AGENT_PROJECT_TRUST_REQUIRED"
    assert not result.success and not result.build_verified and builder.calls == 0
    assert result.build_result["status"] == "blocked"
    assert result.build_result["execution_attempted"] is False
    assert (tmp_path / "src/main.cpp").read_text() == "void setup() {}\nvoid loop() {}\n"
    assert [event for event, _ in events].count("BUILD_BLOCKED") == 1
    assert not any(event in {"BUILD_STARTED", "BUILD_FAILED", "BUILD_REPAIR_STARTED"} for event, _ in events)
    assert result.task_state["next_action"] == "Allow local builds for this project, then verify the saved draft."


def test_trust_revoked_during_preflight_prevents_the_compiler_launch(tmp_path):
    trusted = [True]
    class RevokingBuilder(Builder):
        async def validate_dependencies(self, root):
            trusted[0] = False
            return {"valid": True}
    async def scenario():
        builder = RevokingBuilder()
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder,
            planner=SequencePlanner([]), progress=progress, policy=BUILD_REPAIR_PROFILE.policy(),
            project_trust_getter=lambda: trusted[0], task_contract={"requirements": [{"id": "req-1", "text": "Build source"}]})
        observation = await loop._build_once(None)
        assert observation["output"]["error_code"] == "AGENT_PROJECT_TRUST_REQUIRED"
        assert builder.calls == 0 and not loop.build_passed
    asyncio.run(scenario())


def test_api_permission_block_preserves_draft_then_verifies_without_a_model(tmp_path, monkeypatch):
    from pathlib import Path
    from backend.main import execute_prompt
    from tests.api.test_api_routes import make_client, successful_build
    from tests.api.test_product_agent_runtime_api import setup_env, wait_run_status

    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    client, project_id, builder = make_client(tmp_path, execute_prompt_fn=execute_prompt)
    with client as api:
        owner = api.app.state.services.product_agent_service.orchestrator
        owner.auto_recovery_enabled = False
        planner = SequencePlanner([plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n"), plan(final=True)])
        monkeypatch.setattr(owner.provider_registry, "resolve", lambda *args, **kwargs: planner)
        async def build_stage(project, *, environment=None, **kwargs):
            builder.calls.append((str(project), environment))
            return successful_build(Path(project) / ".pio/build/esp32dev/firmware.bin")
        monkeypatch.setattr(builder, "build", build_stage)
        started = api.post("/agent-runtime/runs", json={"project_id": project_id,
            "instruction": "Update and build ESP32 firmware", "provider_id": "fake_planner", "autonomy": "build_only"})
        assert started.status_code == 202, started.text
        first = wait_run_status(api, started.json()["run"]["run_id"], {"blocked", "failed"})
        assert first["status"] == "blocked", first
        assert first["classification"] == "AGENT_PROJECT_TRUST_REQUIRED"
        assert first["stage_statuses"]["build"] == "blocked"
        assert first["job"]["blocking_reason"] == "AGENT_PROJECT_TRUST_REQUIRED"
        assert first["failure"]["category"] == "policy_rejection"
        assert first["change_set_id"] and first["recovery"]["actions"]["inspect"]["allowed"]
        assert not first["recovery"]["actions"]["verify_locally"]["allowed"]
        assert len(builder.calls) == 0
        count = len(planner.seen_results)
        budget = owner.jobs.get(first["job_id"])["consumed"].copy()
        route = f"/agent-runtime/runs/{first['run_id']}/verify-draft"
        body = {"idempotency_key": "permission-verification-once"}
        assert api.post(route, json=body).status_code == 409
        assert api.post(f"/agent-runtime/projects/{project_id}/trust", json={"trusted": True}).status_code == 200
        refreshed = api.get(f"/agent-runtime/jobs/{first['job_id']}").json()
        assert refreshed["recovery"]["actions"]["verify_locally"]["allowed"]
        assert owner.jobs.get(first["job_id"])["consumed"] == budget
        assert len(planner.seen_results) == count and len(builder.calls) == 0
        def reject_model(*args, **kwargs):
            raise AssertionError("Verifying a saved draft must not invoke a model")
        monkeypatch.setattr(owner.provider_registry, "resolve", reject_model)
        verified = api.post(route, json=body)
        assert verified.status_code == 202, verified.text
        final = wait_run_status(api, verified.json()["run"]["run_id"], {"completed_with_warning", "failed", "blocked"})
        assert final["classification"] == "DRAFT_BUILD_VERIFIED", final
        assert final["build_verified"] and final["active_workspace_unchanged"]
        assert not final.get("hardware_verified", False)
        assert len(builder.calls) == 1
        assert api.post(route, json=body).json()["run"]["run_id"] == final["run_id"]
        assert len(builder.calls) == 1
