import pytest
from backend.api.routes.agent_workspace import _allowed,_phase,_stage_done

def test_backend_derives_only_state_allowed_actions():
 assert {item["id"] for item in _allowed("awaiting_context_consent",{},False)}=={"approve_context","decline_context","cancel"}
 assert {item["id"] for item in _allowed("routing",{},False)}=={"route","cancel"}
 assert {item["id"] for item in _allowed("generating",{},False)}=={"generate_review","cancel"}
 assert _allowed("completed",{},False)==[]

def test_review_action_requires_backend_review_artifact():
 assert {item["id"] for item in _allowed("awaiting_review",{},False)}=={"cancel"}
 assert {item["id"] for item in _allowed("awaiting_review",{},True)}=={"inspect_review","approve_review","cancel"}

def test_backend_execution_status_projection():
 assert _phase("building","build")=="active"
 assert _phase("awaiting_flash_approval","flash")=="waiting"
 assert _phase("completed","monitor")=="completed"
 assert _phase("failed","build")=="failed"

def test_plan_completion_is_derived_from_backend_state():
 stages=["preparing_context","routing","generating","completed"]
 assert _stage_done("routing","generating",stages) is True
 assert _stage_done("completed","generating",stages) is False

@pytest.mark.asyncio
async def test_fail_closed_apply_does_not_mutate_or_advance(tmp_path):
 from types import SimpleNamespace
 from datetime import datetime,timedelta,timezone
 from backend.agent_runtime.agent_adapter import ApprovedBoundedContext,deterministic_fake_adapter
 from backend.agent_runtime.workflow_engine import AgentWorkflowEngine
 from backend.api.errors import APIError
 from backend.api.routes.agent_workspace import FailClosedForgeXExecutors,RunAction,action
 from backend.bridges.diff_service import BridgeDiffService
 from backend.bridges.sandbox_service import BridgeSandboxService
 from backend.state.domain_persistence import DomainDatabase,DomainRepository
 db=DomainDatabase(tmp_path/"workspace.db");engine=AgentWorkflowEngine(db,FailClosedForgeXExecutors());digest="a"*64
 workspace=tmp_path/"workspace";workspace.mkdir();(workspace/"main.txt").write_text("safe\n")
 reviews=BridgeDiffService();adapter=deterministic_fake_adapter(sandboxes=BridgeSandboxService(tmp_path/"sandboxes"),reviews=reviews)
 engine.request(run_id="run",idempotency_key="request-key",request_hash=digest)
 engine.bounded_context(run_id="run",context=ApprovedBoundedContext("safe"),context_hash=digest,requires_disclosure_consent=False)
 engine.routed(run_id="run",decision_hash=digest)
 review=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2)
 engine.submit_review(run_id="run",review_id=review.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
 DomainRepository(db).insert("workflow_steps",{"run_id":"run","step_id":"agent_request","ordinal":0,"status":"prepared","version":0,"request_hash":digest,"project_id":"project"})
 request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(agent_workflow_engine=engine,workflow_database=db)))
 current=engine.machine.get("run")
 with pytest.raises(APIError) as captured:
  await action("run",RunAction(expected_state=current.state.value,expected_version=current.version,action="approve_apply"),request,"apply-key")
 assert captured.value.status_code==503 and captured.value.code=="AGENT_APPLY_EXECUTOR_DISABLED"
 assert engine.machine.get("run").state.value=="awaiting_apply_approval"
