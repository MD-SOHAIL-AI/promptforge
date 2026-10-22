"""Recovery boundaries: intake, persisted job, selection and endpoint health."""
from dataclasses import replace

from backend.agent_runtime.api_planner_provider import ApiPlannerClassification, ApiPlannerError
from backend.agent_runtime.turn_router import AgentTurnIntent, AgentTurnRouter
from backend.services.llm_service import LLMProvider, LLMProviderError
from tests.api.test_api_routes import make_client
from tests.api.test_product_agent_runtime_api import setup_env, wait_run_status


def test_intake_outage_is_visible_and_costs_follow_clarification_into_job(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    api, project_id, _ = make_client(tmp_path)
    with api:
        owner = api.app.state.services.product_agent_service.orchestrator
        owner.auto_recovery_enabled = False
        api.app.state.services.model_router_service.registry.configure_provider("ollama", {"enabled": True, "default_model": "model"})
        request = "blorp OLED or TFT"
        assert AgentTurnRouter().route(request).intent is AgentTurnIntent.UNKNOWN
        attempts = []
        async def interpret(planner, content, decision):
            # Exercise the exact persistent reservation bound by the API.
            planner._run_budget["_reserve"]({"requests": 1, "output_tokens": 100, "input_bytes": 50})
            attempts.append(content)
            if len(attempts) == 1:
                raise ApiPlannerError(ApiPlannerClassification.MODEL_UNAVAILABLE, "PROVIDER_UNAVAILABLE", {"http_status": 503})
            return replace(decision, intent=AgentTurnIntent.GENERATE, requires_workspace=True,
                requires_write=True, routing_source="model_validated", requested_actions=("generate",)), {
                "choices": [{"id": "display", "options": ["OLED", "TFT"]}]}
        monkeypatch.setattr("backend.agent_runtime.task_understanding.interpret_task", interpret)
        async def executor(request):
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "endpoint unavailable"}
        monkeypatch.setattr(owner, "autonomous_executor", executor)
        session = api.post("/agent-runtime/sessions", json={"project_id": project_id, "provider_id": "ollama"}).json()["session"]
        route = f"/agent-runtime/sessions/{session['session_id']}/messages"
        first = api.post(route, json={"content": request, "provider_id": "ollama"})
        assert first.status_code == 202, first.text
        assert first.json()["run"] is None
        message = first.json()["session"]["messages"][-1]
        assert message["metadata"]["failure_code"] == "PROVIDER_UNAVAILABLE"
        assert "selected model failed" in message["content"]
        second = api.post(route, json={"content": request, "provider_id": "ollama"})
        assert second.status_code == 202, second.text
        pending = second.json()["session"]["pending_clarification"]
        assert pending["intake_id"] == message["metadata"]["intake_id"]
        reply = api.post(route, json={"content": "OLED"})
        assert reply.status_code == 202, reply.text
        run = wait_run_status(api, reply.json()["run"]["run_id"], {"paused_model_unavailable"})
        job = owner.jobs.get(run["job_id"])
        assert job["intake_id"] == pending["intake_id"]
        assert job["consumed"]["requests"] == 2
        assert job["consumed"]["output_tokens"] == 200
        assert len(attempts) == 2  # Answering clarification does not re-interpret.


def test_job_model_switch_is_idempotent_and_retains_budget(tmp_path, monkeypatch):
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    api, project_id, _ = make_client(tmp_path)
    with api:
        owner = api.app.state.services.product_agent_service.orchestrator
        owner.auto_recovery_enabled = False
        models = api.app.state.services.model_router_service
        models.registry.configure_provider("ollama", {"enabled": True, "default_model": "model"})
        async def executor(request):
            await request.progress_callback("turn.settled", {"turn": 1, "_resume_checkpoint": {
                "turn": 1, "observations": [], "plan": [], "tool_calls_used": 0, "elapsed_seconds": 0}})
            return {"success": False, "classification": "PROVIDER_TIMEOUT", "message": "endpoint unavailable"}
        monkeypatch.setattr(owner, "autonomous_executor", executor)
        started = api.post("/agent-runtime/runs", json={"project_id": project_id,
            "instruction": "update and build firmware", "provider_id": "fake_planner", "autonomy": "build_only"})
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"paused_model_unavailable"})
        assert run["recovery"]["eligible"], run
        owner.jobs.mutate(run["job_id"], lambda value: value["consumed"].update(requests=7, output_tokens=1234))
        route = f"/agent-runtime/jobs/{run['job_id']}/resume"
        body = {"idempotency_key": "switch-once", "checkpoint_id": run["recovery"]["checkpoint_id"],
            "provider_id": "ollama", "model_id": "model"}
        switched = api.post(route, json=body)
        assert switched.status_code == 202, switched.text
        replacement = wait_run_status(api, switched.json()["run"]["run_id"], {"paused_model_unavailable"})
        assert replacement["provider_id"] == "ollama" and replacement["model_id"] == "model"
        assert replacement["job_id"] == run["job_id"]
        replay = api.post(route, json=body)
        assert replay.status_code == 202 and replay.json()["run"]["run_id"] == replacement["run_id"]
        assert api.post(route, json={**body, "model_id": "different-model"}).status_code == 409
        assert api.post(route, json={**body, "idempotency_key": "new", "checkpoint_id": "0" * 64}).status_code == 409
        assert owner.jobs.get(run["job_id"])["consumed"]["requests"] == 7
        assert owner.jobs.get(run["job_id"])["consumed"]["output_tokens"] == 1234
        identity = models._circuit_identity("ollama", "model")
        models.circuit._settle(identity, None, LLMProviderError("outage", provider=LLMProvider.OPENAI, status_code=503, retryable=True,
            details={"retry_after_ms": 120000}))
        monkeypatch.setattr(models, "generate_model", lambda *_: (_ for _ in ()).throw(AssertionError("health must not generate")))
        health = api.get("/models/providers/ollama/endpoint-health?model_id=model")
        assert health.status_code == 200, health.text
        assert health.json()["state"] == "open" and health.json()["retry_after_ms"] > 0
        assert api.get("/models/providers/missing/endpoint-health").status_code == 404
