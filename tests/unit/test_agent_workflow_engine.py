from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from backend.agent_runtime.agent_adapter import ApprovedBoundedContext,deterministic_fake_adapter
from backend.agent_runtime.workflow_engine import (AgentWorkflowEngine,ApprovalBindingMismatch,ApprovalExpired,CodingWorkflowCompatibilityFacade,ExecutionOutcome,FlashApprovalBinding,HardwareSafetyDenied)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.state.domain_persistence import DomainDatabase
from backend.state.durable_workflow import WorkflowState
H="a"*64;DEVICE="b"*64;PORT="c"*64;BOARD="d"*64;COMMAND="e"*64
class FakeExecutors:
 def __init__(self,builds=None,flash_safe=True):self.builds=list(builds or [True]);self.flash_safe=flash_safe;self.calls=[]
 def preflight_rollback_apply(self,**kw):self.calls.append("preflight_rollback_apply");return ExecutionOutcome(True,"1"*64,"applied")
 def build(self,**kw):self.calls.append("build");ok=self.builds.pop(0);return ExecutionOutcome(ok,"2"*64 if ok else "3"*64,"built" if ok else "repairable",not ok)
 def flash(self,**kw):self.calls.append("flash");return ExecutionOutcome(self.flash_safe,"4"*64,"flashed" if self.flash_safe else "hardware denied")
 def monitor(self,**kw):self.calls.append("monitor");return ExecutionOutcome(True,"5"*64,"bounded monitor")
 def verify(self,**kw):self.calls.append("verify");return ExecutionOutcome(True,"6"*64,"verified")
def setup(tmp_path,executors=None):
 db=DomainDatabase(tmp_path/"workflow.db");ex=executors or FakeExecutors();engine=AgentWorkflowEngine(db,ex);workspace=tmp_path/"workspace";workspace.mkdir(exist_ok=True);(workspace/"main.txt").write_text("safe\n");adapter=deterministic_fake_adapter(sandboxes=BridgeSandboxService(tmp_path/"sandboxes"),reviews=BridgeDiffService());return db,engine,ex,workspace,adapter
def restart(db,ex):return AgentWorkflowEngine(DomainDatabase(db.path),ex)
def begin(engine):
 engine.request(run_id="run",idempotency_key="request",request_hash=H);engine.bounded_context(run_id="run",context=ApprovedBoundedContext("safe request"),context_hash=H,requires_disclosure_consent=True);assert engine.machine.get("run").state is WorkflowState.AWAITING_CONTEXT_CONSENT;engine.consent(run_id="run",approved=True,recipient_hash=H);engine.routed(run_id="run",decision_hash=H)

@pytest.mark.asyncio
async def test_full_fake_flow_and_restart_at_waiting_states(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path);begin(engine);engine=restart(db,ex);result=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2);assert engine.machine.get("run").state is WorkflowState.AWAITING_REVIEW
 engine.submit_review(run_id="run",review_id=result.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));engine=restart(db,ex);assert engine.machine.get("run").state is WorkflowState.AWAITING_APPLY_APPROVAL
 engine.approve_apply(run_id="run",review_id=result.review_id,workspace=workspace);engine=restart(db,ex);assert engine.machine.get("run").state is WorkflowState.AWAITING_BUILD
 build=engine.build(run_id="run",workspace=workspace);engine=restart(db,ex);assert engine.machine.get("run").state is WorkflowState.AWAITING_FLASH_APPROVAL
 binding=FlashApprovalBinding(build.artifact_hash,DEVICE,PORT,BOARD,COMMAND);engine.approve_flash(run_id="run",binding=binding,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));engine=restart(db,ex);engine.flash(run_id="run",workspace=workspace,binding=binding);engine=restart(db,ex);assert engine.machine.get("run").state is WorkflowState.AWAITING_MONITOR
 engine.monitor(run_id="run",port_hash=PORT);engine=restart(db,ex);assert engine.machine.get("run").state is WorkflowState.VERIFYING
 report=engine.verify(run_id="run",workspace=workspace);assert report.status=="completed" and ex.calls==["preflight_rollback_apply","build","flash","monitor","verify"]

@pytest.mark.asyncio
async def test_bounded_repair_creates_new_review_and_repeats_approval(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path,FakeExecutors(builds=[False,True]));begin(engine);first=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2);engine.submit_review(run_id="run",review_id=first.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));engine.approve_apply(run_id="run",review_id=first.review_id,workspace=workspace);engine.build(run_id="run",workspace=workspace);assert engine.machine.get("run").state is WorkflowState.REPAIRING
 second=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("repair"),timeout_seconds=2,repair=True);assert second.review_id!=first.review_id and engine.machine.get("run").state is WorkflowState.AWAITING_REVIEW
 engine.submit_review(run_id="run",review_id=second.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));assert engine.machine.get("run").state is WorkflowState.AWAITING_APPLY_APPROVAL

@pytest.mark.asyncio
async def test_apply_approval_expiry_fails_closed(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path);begin(engine);review=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2);engine.submit_review(run_id="run",review_id=review.review_id,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
 with pytest.raises(ApprovalExpired):engine.approve_apply(run_id="run",review_id=review.review_id,workspace=workspace)
 assert engine.machine.get("run").state is WorkflowState.AWAITING_APPLY_APPROVAL and "preflight_rollback_apply" not in ex.calls

@pytest.mark.asyncio
async def test_wrong_device_or_port_binding_denied_before_hardware(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path);begin(engine);review=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2);engine.submit_review(run_id="run",review_id=review.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));engine.approve_apply(run_id="run",review_id=review.review_id,workspace=workspace);build=engine.build(run_id="run",workspace=workspace);approved=FlashApprovalBinding(build.artifact_hash,DEVICE,PORT,BOARD,COMMAND);engine.approve_flash(run_id="run",binding=approved,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));wrong=FlashApprovalBinding(build.artifact_hash,"f"*64,PORT,BOARD,COMMAND)
 with pytest.raises(ApprovalBindingMismatch):engine.flash(run_id="run",workspace=workspace,binding=wrong)
 assert "flash" not in ex.calls

def test_compatibility_facade_exposes_legacy_commands(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path);facade=CodingWorkflowCompatibilityFacade(engine);assert facade.get_run if hasattr(facade,"get_run") else False

@pytest.mark.asyncio
async def test_hardware_executor_safety_denial_is_terminal(tmp_path):
 db,engine,ex,workspace,adapter=setup(tmp_path,FakeExecutors(flash_safe=False));begin(engine);review=await engine.generate_review(run_id="run",adapter=adapter,workspace=workspace,context=ApprovedBoundedContext("safe"),timeout_seconds=2);engine.submit_review(run_id="run",review_id=review.review_id,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5));engine.approve_apply(run_id="run",review_id=review.review_id,workspace=workspace);build=engine.build(run_id="run",workspace=workspace);binding=FlashApprovalBinding(build.artifact_hash,DEVICE,PORT,BOARD,COMMAND);engine.approve_flash(run_id="run",binding=binding,expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
 with pytest.raises(HardwareSafetyDenied):engine.flash(run_id="run",workspace=workspace,binding=binding)
 assert engine.machine.get("run").state is WorkflowState.FAILED and "monitor" not in ex.calls
