"""AGY/Antigravity AgentAdapter consolidating explicit managed execution modes."""
from __future__ import annotations
import asyncio,json,shutil,threading
from dataclasses import dataclass,field
from enum import Enum
from pathlib import Path
from typing import Any
from .agent_adapter import (AdapterFailure,AdapterReadiness,AdapterResult,AgentAdapterDescriptor,AgentAdapterError,ApprovedBoundedContext,UntrustedProposal)
from .api_agent_contracts import API_CODING_AGENT_SCHEMA_VERSION,parse_api_coding_agent_response
from backend.connection_registry import AuthState, ConnectionRegistry
from backend.bridges.agy_scratch_project_import import AGYScratchImportClassification,AGYScratchProjectImportService,ImportReviewContext
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.providers.antigravity_cli import AntigravityCliDetector
from backend.bridges.providers.antigravity_runner import AntigravitySandboxRunner
from backend.bridges.sandbox_service import BridgeSandboxService

class AGYExecutionMode(str,Enum):SCRATCH_GENERATION="scratch_generation";EXPLICIT_SCRATCH_IMPORT="explicit_scratch_import";SANDBOX_EXECUTION="sandbox_execution"
@dataclass(slots=True)
class AGYPreparedRun:
 run_id:str;mode:AGYExecutionMode;active_workspace:Path;bound_path:Path;context:ApprovedBoundedContext;cancelled:threading.Event=field(default_factory=threading.Event);task:asyncio.Task|None=None;legacy_run_id:str|None=None;review_id:str|None=None;proposal:UntrustedProposal|None=None;owned_path:bool=False
class AGYCliAgentAdapter:
 adapter_id="agy_cli"
 def __init__(self,*,detector:AntigravityCliDetector|None=None,runner:AntigravitySandboxRunner,importer:AGYScratchProjectImportService,sandboxes:BridgeSandboxService,reviews:BridgeDiffService,managed_generation_root:Path,enabled:bool=False,health_ok:bool=True,connections:ConnectionRegistry|None=None):self.detector,self.runner,self.importer,self.sandboxes,self.reviews=detector,runner,importer,sandboxes,reviews;self.managed_generation_root=Path(managed_generation_root).resolve();self.enabled,self.health_ok=enabled,health_ok;self.connections=connections
 @property
 def descriptor(self):return AgentAdapterDescriptor("agy_cli","Google Antigravity / AGY CLI","local_cli",self.readiness(),True,True,True,frozenset(),"Production eligibility remains disabled pending containment evidence.",False)
 def status_dimensions(self):
  if self.connections is not None:
   record=self.connections.refresh_status(self.connections.AGY_ID)
   return {"detected":record.detected,"auth_state":record.auth_state.value,"compatibility":"compatible" if record.version else "unknown","enabled":bool(self.enabled and record.enabled),"health":record.transport_status.value,"production_eligible":False,"version":record.version}
  if self.detector is None:return {"detected":False,"auth_state":"auth_unknown","compatibility":"unknown","enabled":False,"health":"unknown","production_eligible":False,"version":None}
  detected=self.detector.detect();auth={"authenticated":"authenticated","unauthenticated":"signed_out","not_installed":"not_installed"}.get(detected.auth_status,"auth_unknown")
  return {"detected":detected.installed,"auth_state":auth,"compatibility":"compatible" if detected.version else "unknown","enabled":self.enabled,"health":"ready" if self.health_ok and detected.installed else "unavailable","production_eligible":False,"version":detected.version}
 def readiness(self):
  s=self.status_dimensions()
  if not s["detected"]:return AdapterReadiness.UNAVAILABLE
  if not s["enabled"]:return AdapterReadiness.DISABLED
  if s["auth_state"]!="authenticated":return AdapterReadiness.BLOCKED
  return AdapterReadiness.READY if s["health"]=="ready" else AdapterReadiness.BLOCKED
 def prepare_run(self,*,run_id:str,workspace:Path,context:ApprovedBoundedContext):return self.prepare_mode(run_id=run_id,workspace=workspace,context=context,mode=AGYExecutionMode.SANDBOX_EXECUTION)
 def prepare_mode(self,*,run_id:str,workspace:Path,context:ApprovedBoundedContext,mode:AGYExecutionMode,bound_path:Path|None=None):
  if self.readiness() is not AdapterReadiness.READY:raise AgentAdapterError(AdapterFailure.DISABLED,"AGY adapter execution is disabled or authentication is unverified")
  active=Path(workspace).resolve()
  if mode is AGYExecutionMode.EXPLICIT_SCRATCH_IMPORT:
   if bound_path is None:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"explicit scratch path is required")
   path=Path(bound_path).resolve();owned=False
  elif mode is AGYExecutionMode.SCRATCH_GENERATION:
   if bound_path is None:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"precomputed managed generation path is required")
   path=self._bound_managed(bound_path,self.managed_generation_root);path.mkdir(parents=True,exist_ok=False);owned=True
   self._write_context(path,context)
  else:
   base=self.sandboxes.sandbox_root.resolve();path=self._bound_managed(base/run_id,base);path.mkdir(parents=True,exist_ok=False);owned=True;self._write_context(path,context)
  if path==active:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"active workspace cannot be an AGY execution path")
  return AGYPreparedRun(run_id,mode,active,path,context,owned_path=owned)
 async def execute_turn(self,run:AGYPreparedRun,*,timeout_seconds:float):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"AGY run cancelled")
  async def invoke():
   if run.mode is AGYExecutionMode.EXPLICIT_SCRATCH_IMPORT:
    result=await asyncio.to_thread(self.importer.import_project,str(run.bound_path),review_context=ImportReviewContext(provider_id="agy_cli",source="explicit_scratch_path",execution_mode=run.mode.value,run_id=run.run_id,expected_source_name=run.bound_path.name))
    if result.classification!=AGYScratchImportClassification.PASS or not result.review_id:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"AGY scratch import failed validation")
    return result.review_id
   legacy=await asyncio.to_thread(self.runner.start_run_in_sandbox,run_id=run.run_id,sandbox_root=run.bound_path,prompt=run.context.instruction,timeout_seconds=max(1,int(timeout_seconds)))
   run.legacy_run_id=legacy.run_id
   while True:
    if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"AGY run cancelled")
    observed=await asyncio.to_thread(self.runner.get_run,legacy.run_id)
    if observed.status in {"review_ready","completed"}:
     if not observed.review_id:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"AGY review is missing")
     return observed.review_id
    if observed.status in {"cancelled"}:raise AgentAdapterError(AdapterFailure.CANCELLED,"AGY run cancelled")
    if observed.status in {"failed","failed_timeout","completed_no_changes"}:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"AGY run failed safely")
    await asyncio.sleep(.05)
  try:run.task=asyncio.create_task(invoke());review_id=await asyncio.wait_for(run.task,timeout_seconds+1)
  except asyncio.TimeoutError as exc:self.cancel(run);raise AgentAdapterError(AdapterFailure.TIMED_OUT,"AGY run timed out") from exc
  except asyncio.CancelledError as exc:run.cancelled.set();raise AgentAdapterError(AdapterFailure.CANCELLED,"AGY run cancelled") from exc
  finally:run.task=None
  run.review_id=review_id;run.proposal=UntrustedProposal(self._proposal(review_id));return run.proposal
 def cancel(self,run):
  run.cancelled.set()
  if run.legacy_run_id:
   try:self.runner.cancel_run(run.legacy_run_id)
   except Exception:pass
  if run.task and not run.task.done():run.task.cancel()
 def collect_result(self,run):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"AGY run cancelled")
  if not run.review_id or not run.proposal:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"AGY review result unavailable")
  review=self.reviews.get_review(run.review_id)
  if review.status!="pending":raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"AGY result is not a pending review")
  return AdapterResult(run.run_id,run.review_id,tuple(x.path for x in review.changed_files),review.summary)
 def cleanup(self,run):
  if not run.owned_path:return False
  root=(self.managed_generation_root if run.mode is AGYExecutionMode.SCRATCH_GENERATION else self.sandboxes.sandbox_root).resolve();path=run.bound_path.resolve()
  try:path.relative_to(root)
  except ValueError as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"cleanup path escaped managed root") from exc
  if path==root:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"cleanup refused managed root")
  if path.exists():shutil.rmtree(path);return True
  return False
 def safe_diagnostics(self):return {"adapter_id":"agy_cli","auth_type":"cli_owned_session","auth_files_read":False,"tokens_read":False,"raw_auth_output_persisted":False,"execution_modes":[x.value for x in AGYExecutionMode],"arbitrary_folder_discovery":False,"explicit_path_binding":True,"active_workspace_mutation":False,"apply":False,"build":False,"flash":False,"monitor":False,**self.status_dimensions()}
 def _proposal(self,review_id):
  review=self.reviews.get_review(review_id);files=[]
  for changed in review.changed_files:
   target=Path(review.workspace_root)/changed.path
   if changed.change_type=="deleted":files.append({"path":changed.path,"action":"delete"})
   else:files.append({"path":changed.path,"action":"create_or_update","content":target.read_text(encoding="utf-8")})
  return parse_api_coding_agent_response(json.dumps({"schema_version":API_CODING_AGENT_SCHEMA_VERSION,"summary":review.summary or "AGY review proposal","files":files,"commands_suggested":[],"risks":[],"next_steps":[]}),allow_delete=True)
 @staticmethod
 def _bound_managed(candidate,root):
  path=Path(candidate).resolve();root=Path(root).resolve()
  try:path.relative_to(root)
  except ValueError as exc:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"bound path is outside managed root") from exc
  if path==root:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"bound path cannot be managed root")
  return path
 @staticmethod
 def _write_context(root,context):
  for relative,content in context.files.items():
   target=(root/relative).resolve();target.relative_to(root.resolve());target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding="utf-8",newline="\n")
