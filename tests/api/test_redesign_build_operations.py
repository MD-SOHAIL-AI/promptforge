import time
import pytest
from tests.api.test_api_routes import make_client


def test_addressable_build_is_idempotent_and_invalidates_after_source_edit(tmp_path):
    client, project_id, builder = make_client(tmp_path)
    calls = []
    original = builder.build
    async def counted(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)
    builder.build = counted
    with client:
        headers = {"Idempotency-Key": "build-operation-test-0001"}
        body = {"project_id": project_id}
        accepted = client.post("/build/operations", json=body, headers=headers)
        assert accepted.status_code == 202, accepted.text
        for _ in range(100):
            observed = client.get("/build/operations/" + headers["Idempotency-Key"])
            assert observed.status_code == 200, observed.text
            if observed.json()["state"] not in {"queued", "running"}:
                break
            time.sleep(.01)
        assert observed.json()["state"] == "passed", observed.text
        assert client.post("/build/operations", json=body, headers=headers).status_code == 202
        assert len(calls) == 1
        assert observed.json()["result"]["result"]["metadata"]["operation_id"] == headers["Idempotency-Key"]
        edit = client.put("/files", json={"project_id":project_id, "path":"src/main.cpp", "content":"void setup() {}\nvoid loop() {}\n// changed", "expected_revision":client.get("/files/content",params={"project_id":project_id,"path":"src/main.cpp"}).json()["revision"]})
        assert edit.status_code == 200, edit.text
        assert client.get("/build/operations/" + headers["Idempotency-Key"]).json()["state"] == "stale"
        assert len(calls) == 1


def test_readiness_does_not_generate_model_output(tmp_path):
    client, project_id, _ = make_client(tmp_path)
    with client:
        result = client.get(f"/projects/{project_id}/readiness?provider_id=not-configured")
        assert result.status_code == 200, result.text
        value = result.json()
        assert value["workspace"]["available"]
        assert not value["capabilities"]["generate"]
        assert value["qualification"]["provider"] == "not_qualified"


@pytest.mark.parametrize("ini", ["", "board = esp32dev\n", "[env:esp32dev\nboard = esp32dev\n"])
def test_incomplete_or_invalid_configuration_does_not_crash_readiness(tmp_path, ini):
    from pathlib import Path
    client, project_id, _ = make_client(tmp_path)
    with client:
        root = Path(client.get(f"/projects/{project_id}").json()["project_path"])
        (root / "platformio.ini").write_text(ini)
        result = client.get(f"/projects/{project_id}/readiness?provider_id=ollama")
        assert result.status_code == 200, result.text
        value = result.json()
        assert value["workspace"]["available"] and value["capabilities"]["generate"]
        assert not value["configuration"]["valid"] and not value["capabilities"]["build"]
        assert value["configuration"]["warnings"]
        assert client.get(f"/projects/{project_id}/profile").status_code == 422


@pytest.mark.parametrize("environment_evidence", [
    {"environment":"debug"},
    {"environment":None, "metadata":{"environment":"debug"}},
    {"environment":None, "artifact_manifest":{"environment":"debug"}},
])
def test_agent_build_evidence_checks_actual_source_and_environment(tmp_path, environment_evidence):
    from backend.agent_runtime.product_agent_service import ProductAgentRun
    client, project_id, _ = make_client(tmp_path)
    with client:
        owner = client.app.state.services.agent_orchestrator
        root = client.get(f"/projects/{project_id}").json()["project_path"]
        revision = owner.change_service.inspect_workspace(root).revision
        run = ProductAgentRun("evidence-run", project_id, "fake_planner", status="completed",
            build_verified=True, active_workspace_unchanged=False,
            build_result={"success":True,"workspace_revision":revision,**environment_evidence})
        owner._runs[run.run_id] = run
        assert client.get(f"/agent-runtime/runs/{run.run_id}/build-evidence?environment=debug").json()["state"] == "passed"
        assert client.get(f"/agent-runtime/runs/{run.run_id}/build-evidence?environment=release").json()["state"] == "stale"
        edit = client.put("/files", json={"project_id":project_id, "path":"src/main.cpp", "content":"// edited after compilation", "expected_revision":client.get("/files/content",params={"project_id":project_id,"path":"src/main.cpp"}).json()["revision"]})
        assert edit.status_code == 200, edit.text
        assert client.get(f"/agent-runtime/runs/{run.run_id}/build-evidence?environment=debug").json()["state"] == "stale"
