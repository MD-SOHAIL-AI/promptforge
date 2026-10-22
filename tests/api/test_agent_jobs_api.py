from tests.api.test_product_agent_runtime_api import setup_env, wait_run
from tests.api.test_api_routes import make_client


def test_job_api_shares_budget_and_preserves_completed_run(monkeypatch,tmp_path):
    setup_env(monkeypatch,tmp_path,enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2","1")
    api,project_id,_=make_client(tmp_path)
    with api:
        started=api.post("/agent-runtime/runs",json={"project_id":project_id,"provider_id":"fake_planner","instruction":"inspect source","autonomy":"read_only"})
        assert started.status_code==202,started.text
        run=wait_run(api,started.json()["run"]["run_id"])
        job_id=run["job_id"]
        job=api.get("/agent-runtime/jobs/"+job_id).json()["job"]
        assert "workspace_root" not in job
        assert api.get("/agent-runtime/jobs?project_id="+project_id).json()["jobs"][0]["job_id"]==job_id
        prior=run["status"]
        assert api.post("/agent-runtime/jobs/"+job_id+"/pause").status_code==409
        assert api.get("/agent-runtime/runs/"+run["run_id"]).json()["run"]["status"]==prior
        body={"idempotency_key":"test-extension-1"}
        first=api.post("/agent-runtime/jobs/"+job_id+"/budget",json=body)
        second=api.post("/agent-runtime/jobs/"+job_id+"/budget",json=body)
        assert first.status_code==second.status_code==200
        assert first.json()["job"]["budget"]==second.json()["job"]["budget"]
        assert first.json()["job"]["budget"]["limits"]["requests"]==240
        assert api.post("/agent-runtime/jobs/"+job_id+"/budget",json={**body,"requests":1}).status_code==409
        assert api.post("/agent-runtime/jobs/"+job_id+"/recovery-policy",json={"policy":"manual"}).json()["job"]["recovery_policy"]=="manual"
        assert api.post("/agent-runtime/jobs/"+job_id+"/budget",json={"idempotency_key":"invalid","requests":True}).status_code==422


def test_project_trust_and_tool_availability_are_explicit(monkeypatch,tmp_path):
    setup_env(monkeypatch,tmp_path,enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2","1")
    api,project_id,_=make_client(tmp_path)
    with api:
        route="/agent-runtime/projects/"+project_id+"/trust"
        assert api.get(route).json()=={"trusted":False}
        assert api.post(route,json={"trusted":True}).json()=={"trusted":True}
        assert api.post(route,json={"trusted":False}).json()=={"trusted":False}
        assert api.post(route,json={"trusted":"true"}).status_code==422
        tools=api.get("/agent-runtime/tool-catalog").json()["tools"]
        assert next(tool for tool in tools if tool["name"]=="project_doctor")["available"]
        assert not next(tool for tool in tools if tool["name"]=="run_unit_tests")["available"]
        assert api.get("/agent-runtime/jobs/missing").status_code==404


def test_advanced_profile_reaches_the_persisted_job(monkeypatch, tmp_path):
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    api, project_id, _ = make_client(tmp_path)
    with api:
        payload = {"project_id": project_id, "provider_id": "fake_planner",
                   "instruction": "inspect source", "autonomy": "read_only",
                   "execution_profile": "advanced"}
        started = api.post("/agent-runtime/runs", json=payload)
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        job = api.get("/agent-runtime/jobs/" + run["job_id"]).json()["job"]
        assert job["execution_profile"] == "advanced"
        assert job["budget"]["limits"]["elapsed_seconds"] == 2700
        assert job["budget"]["limits"]["requests"] == 360
        invalid = api.post("/agent-runtime/runs", json={**payload, "execution_profile": "unlimited"})
        assert invalid.status_code == 422
