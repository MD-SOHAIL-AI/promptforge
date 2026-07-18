"""Durable adapter-to-ForgeX workflow orchestration with compatibility commands."""
from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Mapping,Protocol
from .agent_adapter import AgentAdapter,AdapterResult,ApprovedBoundedContext
from backend.state.domain_persistence import DomainDatabase,DomainRepository,_decode,_json,_now
from backend.state.durable_workflow import ArtifactPersistenceFailed,AuthoritativeArtifact,DurableWorkflowStateMachine,TransitionResult,WorkflowState

class WorkflowEngineError(RuntimeError):pass
class ApprovalExpired(WorkflowEngineError):pass
class ApprovalBindingMismatch(WorkflowEngineError):pass
class HardwareSafetyDenied(WorkflowEngineError):pass
@dataclass(frozen=True,slots=True)
class ExecutionOutcome:
 success:bool;artifact_hash:str;safe_summary:str;repairable:bool=False
@dataclass(frozen=True,slots=True)
class FlashApprovalBinding:
 artifact_hash:str;device_hash:str;port_hash:str;board_hash:str;command_hash:str
 def digest(self):return _hash("|".join((self.artifact_hash,self.device_hash,self.port_hash,self.board_hash,self.command_hash)))
@dataclass(frozen=True,slots=True)
class WorkflowReport:
 run_id:str;status:str;review_ids:tuple[str,...];artifact_hashes:tuple[str,...];verification_summary:str
class ForgeXExecutors(Protocol):
 def preflight_rollback_apply(self,*,run_id:str,review_id:str,workspace:Path)->ExecutionOutcome:...
 def build(self,*,run_id:str,workspace:Path)->ExecutionOutcome:...
 def flash(self,*,run_id:str,workspace:Path,binding:FlashApprovalBinding)->ExecutionOutcome:...
 def monitor(self,*,run_id:str,port_hash:str)->ExecutionOutcome:...
 def verify(self,*,run_id:str,workspace:Path)->ExecutionOutcome:...

class AgentWorkflowEngine:
 def __init__(self,database:DomainDatabase,executors:ForgeXExecutors,*,max_repairs:int=2):self.database=database;self.machine=DurableWorkflowStateMachine(database);self.repo=DomainRepository(database);self.executors=executors;self.max_repairs=max_repairs
 def request(self,*,run_id:str,idempotency_key:str,request_hash:str)->TransitionResult:
  created=self.machine.create(run_id=run_id,idempotency_key=idempotency_key,actor="user",policy_decision="request_accepted",input_artifact_hashes=(request_hash,));return self._move(run_id,WorkflowState.PREPARING_CONTEXT,"prepare-context",(request_hash,),()) if not created.duplicate else self.machine.get(run_id)
 def bounded_context(self,*,run_id:str,context:ApprovedBoundedContext,context_hash:str,requires_disclosure_consent:bool)->TransitionResult:
  current=self.machine.get(run_id);target=WorkflowState.AWAITING_CONTEXT_CONSENT if requires_disclosure_consent else WorkflowState.ROUTING
  return self._move(run_id,target,"bounded-context",(context_hash,),(),expected=current)
 def consent(self,*,run_id:str,approved:bool,recipient_hash:str)->TransitionResult:
  if not approved: return self.cancel(run_id=run_id,reason_hash=recipient_hash)
  return self._move(run_id,WorkflowState.ROUTING,"disclosure-consent",(recipient_hash,),())
 def routed(self,*,run_id:str,decision_hash:str)->TransitionResult:return self._move(run_id,WorkflowState.GENERATING,"routing-decision",(decision_hash,),())
 async def generate_review(self,*,run_id:str,adapter:AgentAdapter,workspace:Path,context:ApprovedBoundedContext,timeout_seconds:float,repair:bool=False)->AdapterResult:
  current=self.machine.get(run_id)
  if repair:
   if current.state is not WorkflowState.REPAIRING:raise WorkflowEngineError("run is not repairing")
   self._move(run_id,WorkflowState.GENERATING,"repair-generate",(),(),expected=current)
  elif current.state is WorkflowState.VALIDATING:raise ArtifactPersistenceFailed()
  elif current.state is not WorkflowState.GENERATING:raise WorkflowEngineError("run is not generating")
  prepared=adapter.prepare_run(run_id=f"{run_id}-{current.version}",workspace=workspace,context=context);proposal=await adapter.execute_turn(prepared,timeout_seconds=timeout_seconds);proposal_hash=_hash(repr(proposal.proposal));self._move(run_id,WorkflowState.VALIDATING,"proposal-generated",(),(proposal_hash,));result=adapter.collect_result(prepared);review_hash=_hash(result.review_id);self._move_with_artifact(run_id,WorkflowState.AWAITING_REVIEW,"proposal-validated",(proposal_hash,),(review_hash,),kind="review",ref=result.review_id,digest=review_hash);return result
 def submit_review(self,*,run_id:str,review_id:str,expires_at:datetime)->TransitionResult:
  current=self.machine.get(run_id);review_hash=_hash(review_id)
  if current.state is not WorkflowState.AWAITING_REVIEW:raise WorkflowEngineError("run is not awaiting review")
  self._require_artifact(run_id,"review",review_hash,review_id);self._approval(run_id,"apply",review_hash,expires_at,{"review_id":review_id});return self._move(run_id,WorkflowState.AWAITING_APPLY_APPROVAL,"review-ready",(review_hash,),(),expected=current)
 def approve_apply(self,*,run_id:str,review_id:str,workspace:Path)->ExecutionOutcome:
  current=self.machine.get(run_id)
  if current.state is WorkflowState.APPLYING:raise ArtifactPersistenceFailed()
  if current.state is not WorkflowState.AWAITING_APPLY_APPROVAL:raise WorkflowEngineError("run is not awaiting apply approval")
  review_hash=_hash(review_id);self._require_artifact(run_id,"review",review_hash,review_id);self._consume_approval(run_id,"apply",review_hash);lease=self.machine.acquire_lease(run_id=run_id,operation="apply",owner_id="forgex.apply_executor",ttl_seconds=120)
  try:
   self._move(run_id,WorkflowState.APPLYING,"apply-approved",(review_hash,),(),actor="forgex.apply_executor");out=self.executors.preflight_rollback_apply(run_id=run_id,review_id=review_id,workspace=workspace)
   if not out.success:self._move(run_id,WorkflowState.FAILED,"apply-failed",(review_hash,),(out.artifact_hash,),safe=out.safe_summary);return out
   self._move_with_artifact(run_id,WorkflowState.AWAITING_BUILD,"apply-complete",(review_hash,),(out.artifact_hash,),kind="applied",ref=out.artifact_hash,digest=out.artifact_hash,safe=out.safe_summary);return out
  finally:self.machine.release_lease(lease)
 def build(self,*,run_id:str,workspace:Path)->ExecutionOutcome:
  current=self.machine.get(run_id)
  if current.state is WorkflowState.BUILDING:raise ArtifactPersistenceFailed()
  if current.state is not WorkflowState.AWAITING_BUILD:raise WorkflowEngineError("run is not awaiting build")
  self._require_artifact(run_id,"applied");lease=self.machine.acquire_lease(run_id=run_id,operation="build",owner_id="forgex.build_executor",ttl_seconds=300)
  try:
   self._move(run_id,WorkflowState.BUILDING,"build-start",(),(),actor="forgex.build_executor");out=self.executors.build(run_id=run_id,workspace=workspace)
   if out.success:self._move_with_artifact(run_id,WorkflowState.AWAITING_FLASH_APPROVAL,"build-complete",(),(out.artifact_hash,),kind="build",ref=out.artifact_hash,digest=out.artifact_hash,safe=out.safe_summary)
   elif out.repairable and self._repair_count(run_id)<self.max_repairs:self._increment_repair(run_id);self._move_with_artifact(run_id,WorkflowState.REPAIRING,"build-repair",(),(out.artifact_hash,),kind="build",ref=out.artifact_hash,digest=out.artifact_hash,safe=out.safe_summary)
   else:self._move_with_artifact(run_id,WorkflowState.FAILED,"build-failed",(),(out.artifact_hash,),kind="build",ref=out.artifact_hash,digest=out.artifact_hash,safe=out.safe_summary)
   return out
  finally:self.machine.release_lease(lease)
 def approve_flash(self,*,run_id:str,binding:FlashApprovalBinding,expires_at:datetime)->None:
  if self.machine.get(run_id).state is not WorkflowState.AWAITING_FLASH_APPROVAL:raise WorkflowEngineError("run is not awaiting flash approval")
  self._require_artifact(run_id,"build",binding.artifact_hash);self._approval(run_id,"flash",binding.digest(),expires_at,{"binding":binding.__dict__ if hasattr(binding,"__dict__") else {k:getattr(binding,k) for k in binding.__slots__}})
 def flash(self,*,run_id:str,workspace:Path,binding:FlashApprovalBinding)->ExecutionOutcome:
  if self.machine.get(run_id).state is not WorkflowState.AWAITING_FLASH_APPROVAL:raise WorkflowEngineError("run is not awaiting flash approval")
  self._require_build_artifact(run_id,binding.artifact_hash);self._consume_approval(run_id,"flash",binding.digest());lease=self.machine.acquire_lease(run_id=run_id,operation="flash",owner_id="forgex.flash_executor",ttl_seconds=180)
  try:
   self._move(run_id,WorkflowState.FLASHING,"flash-approved",(binding.artifact_hash,binding.device_hash,binding.port_hash,binding.board_hash,binding.command_hash),(),actor="forgex.flash_executor");out=self.executors.flash(run_id=run_id,workspace=workspace,binding=binding)
   if not out.success:self._move(run_id,WorkflowState.FAILED,"flash-safety-denied",(binding.artifact_hash,),(out.artifact_hash,),safe=out.safe_summary);raise HardwareSafetyDenied(out.safe_summary)
   self._move(run_id,WorkflowState.AWAITING_MONITOR,"flash-complete",(binding.artifact_hash,),(out.artifact_hash,),safe=out.safe_summary);return out
  finally:self.machine.release_lease(lease)
 def monitor(self,*,run_id:str,port_hash:str)->ExecutionOutcome:
  lease=self.machine.acquire_lease(run_id=run_id,operation="monitor",owner_id="forgex.monitor_executor",ttl_seconds=60)
  try:self._move(run_id,WorkflowState.MONITORING,"monitor-start",(port_hash,),(),actor="forgex.monitor_executor");out=self.executors.monitor(run_id=run_id,port_hash=port_hash);self._move(run_id,WorkflowState.VERIFYING,"monitor-complete",(port_hash,),(out.artifact_hash,),safe=out.safe_summary);return out
  finally:self.machine.release_lease(lease)
 def verify(self,*,run_id:str,workspace:Path)->WorkflowReport:
  out=self.executors.verify(run_id=run_id,workspace=workspace);target=WorkflowState.COMPLETED if out.success else WorkflowState.FAILED;self._move(run_id,target,"verification",(),(out.artifact_hash,),safe=out.safe_summary);return self.report(run_id,out.safe_summary)
 def skip_monitor(self,*,run_id:str)->TransitionResult:return self._move(run_id,WorkflowState.VERIFYING,"monitor-skipped",(),())
 def cancel(self,*,run_id:str,reason_hash:str)->TransitionResult:
  c=self.machine.get(run_id);return self.machine.cancel(run_id=run_id,expected_state=c.state,expected_version=c.version,idempotency_key=f"cancel-{c.version}-{reason_hash[:8]}",actor="user",policy_decision="cancelled",input_artifact_hashes=(reason_hash,),output_artifact_hashes=())
 def report(self,run_id:str,summary:str="")->WorkflowReport:
  state=self.machine.get(run_id)
  with self.database.connect() as db:rows=db.execute("SELECT artifact_type,storage_reference,content_hash FROM artifacts WHERE run_id=? ORDER BY created_at",(run_id,)).fetchall()
  return WorkflowReport(run_id,state.state.value,tuple(r[1] for r in rows if r[0]=="review"),tuple(r[2] for r in rows),summary)
 def _move(self,run,target,key,inputs,outputs,*,expected=None,actor="forgex.workflow",safe=None):
  c=expected or self.machine.get(run);return self.machine.transition(run_id=run,expected_state=c.state,expected_version=c.version,idempotency_key=f"{key}-{c.version}",actor=actor,policy_decision=key,input_artifact_hashes=inputs,output_artifact_hashes=outputs,target_state=target,safe_message=safe)
 def _move_with_artifact(self,run,target,key,inputs,outputs,*,kind,ref,digest,expected=None,actor="forgex.workflow",safe=None):
  c=expected or self.machine.get(run);artifact=AuthoritativeArtifact(f"{run}.{kind}.{digest}",run,kind,digest,ref)
  return self.machine.transition_with_artifact(artifact=artifact,run_id=run,expected_state=c.state,expected_version=c.version,idempotency_key=f"{key}-{c.version}",actor=actor,policy_decision=key,input_artifact_hashes=inputs,output_artifact_hashes=outputs,target_state=target,safe_message=safe)
 def _require_artifact(self,run,kind,digest=None,ref=None):
  with self.database.connect() as db:ok=db.execute("SELECT 1 FROM artifacts WHERE run_id=? AND artifact_type=? AND (? IS NULL OR content_hash=?) AND (? IS NULL OR storage_reference=?)",(run,kind,digest,digest,ref,ref)).fetchone()
  if not ok:raise ArtifactPersistenceFailed()
 def _approval(self,run,kind,binding,expires,metadata):
  if expires.tzinfo is None:raise WorkflowEngineError("approval expiry must be timezone-aware")
  self.repo.insert("approvals",{"approval_id":f"{run}.{kind}.{binding[:12]}","run_id":run,"step_id":None,"status":"pending","action_code":kind,"idempotency_key":f"{run}.{kind}.{binding}","requested_at":_now(),"resolved_at":None,"binding_hash":binding,"expires_at":expires.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),**metadata})
 def _consume_approval(self,run,kind,binding):
  with self.database.transaction() as db:
   rows=db.execute("SELECT approval_id,payload_json FROM approvals WHERE run_id=? AND action_code=? AND status='pending' ORDER BY requested_at DESC",(run,kind)).fetchall()
   if not rows:raise ApprovalBindingMismatch("approval not found")
   p=_decode(rows[0][1]);
   if p.get("binding_hash")!=binding:raise ApprovalBindingMismatch("approval binding mismatch")
   if datetime.fromisoformat(p["expires_at"].replace("Z","+00:00"))<=datetime.now(timezone.utc):db.execute("UPDATE approvals SET status='expired',resolved_at=? WHERE approval_id=?",(_now(),rows[0][0]));raise ApprovalExpired("approval expired")
   db.execute("UPDATE approvals SET status='approved',resolved_at=? WHERE approval_id=?",(_now(),rows[0][0]))
 def _require_build_artifact(self,run,digest):
  try:self._require_artifact(run,"build",digest)
  except ArtifactPersistenceFailed as exc:raise HardwareSafetyDenied("flash artifact does not match approved build") from exc
 def _repair_count(self,run):
  with self.database.connect() as db:return db.execute("SELECT count(*) FROM workflow_events WHERE run_id=? AND json_extract(payload_json,'$.to_state')='repairing'",(run,)).fetchone()[0]
 def _increment_repair(self,run):return None

class CodingWorkflowCompatibilityFacade:
 """Legacy coding-workflow command names translated to the durable engine."""
 def __init__(self,engine:AgentWorkflowEngine):self.engine=engine
 async def generate_review(self,**kwargs):return await self.engine.generate_review(**kwargs)
 def approve_apply(self,**kwargs):return self.engine.approve_apply(**kwargs)
 def build(self,**kwargs):return self.engine.build(**kwargs)
 async def repair(self,**kwargs):return await self.engine.generate_review(repair=True,**kwargs)
 def approve_flash(self,**kwargs):return self.engine.approve_flash(**kwargs)
 def flash(self,**kwargs):return self.engine.flash(**kwargs)
 def monitor(self,**kwargs):return self.engine.monitor(**kwargs)
 def cancel(self,**kwargs):return self.engine.cancel(**kwargs)
 def get_run(self,run_id):return self.engine.machine.get(run_id)
 def events(self,run_id):return self.engine.repo.list_events(run_id)
def _hash(value:str)->str:return hashlib.sha256(value.encode()).hexdigest()
