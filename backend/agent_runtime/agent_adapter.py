"""Single containment-first AgentAdapter contract and initial adapters."""
from __future__ import annotations
import asyncio
import hashlib
import inspect
import threading
from dataclasses import dataclass,field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any,Awaitable,Callable,Mapping,Protocol
from .api_agent_contracts import (API_CODING_AGENT_SCHEMA_VERSION,ApiCodingAgentContractError,ApiCodingAgentProposal,parse_api_coding_agent_response)
from .product_providers import PRODUCT_SMOKE_CONTENT,PRODUCT_SMOKE_PATH
from ..provider_runtime.templates import match_template
from ..bridges.diff_service import BridgeDiffService,is_safe_relative_path
from ..bridges.sandbox_service import BridgeSandboxService

MAX_CONTEXT_FILES=20;MAX_CONTEXT_FILE_BYTES=32*1024;MAX_CONTEXT_BYTES=128*1024;MAX_INSTRUCTION_CHARS=20_000;MAX_OUTPUT_BYTES=512*1024
_SECRET=("-----begin private key-----","password=","secret=","token=","api_key=","authorization: bearer")
class AdapterReadiness(str,Enum):READY="ready";DISABLED="disabled";BLOCKED="blocked";UNAVAILABLE="unavailable"
class AdapterFailure(str,Enum):INVALID_CONTEXT="invalid_context";DISABLED="disabled";CANCELLED="cancelled";TIMED_OUT="timed_out";UNSAFE_OUTPUT="unsafe_output";OUTPUT_LIMIT="output_limit";UNDECLARED_CHANGE="undeclared_change";ACTIVE_WORKSPACE_CHANGED="active_workspace_changed"
class AgentAdapterError(RuntimeError):
 def __init__(self,code:AdapterFailure,message:str):self.code=code;super().__init__(message)
@dataclass(frozen=True,slots=True)
class AgentAdapterDescriptor:
 adapter_id:str;display_name:str;kind:str;readiness:AdapterReadiness;local_cli:bool=False;managed_sandbox_required:bool=True;returns_untrusted_proposals:bool=True;authorities:frozenset[str]=frozenset();safe_reason:str|None=None;production_eligible:bool=False
 def __post_init__(self):
  forbidden={"apply","build","flash","monitor","approve","active_workspace_mutation","execute_shell","secret_access"}
  if self.authorities&forbidden:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"adapter declares forbidden authority")
  if self.local_cli and not self.managed_sandbox_required:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"local CLI requires managed sandbox")
@dataclass(frozen=True,slots=True)
class ApprovedBoundedContext:
 instruction:str;files:Mapping[str,str]=field(default_factory=dict);consent_id:str="approved"
 def __post_init__(self):
  if not self.instruction.strip() or len(self.instruction)>MAX_INSTRUCTION_CHARS or _secret(self.instruction):raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"instruction is unsafe or unbounded")
  if len(self.files)>MAX_CONTEXT_FILES:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"too many context files")
  total=0;frozen={}
  for path,content in self.files.items():
   if not is_safe_relative_path(path) or not isinstance(content,str) or _secret(content):raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"context contains unsafe path or secret material")
   size=len(content.encode());total+=size
   if size>MAX_CONTEXT_FILE_BYTES:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"context file exceeds limit")
   frozen[path]=content
  if total>MAX_CONTEXT_BYTES:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"context exceeds total limit")
  object.__setattr__(self,"files",MappingProxyType(frozen))
@dataclass(frozen=True,slots=True)
class UntrustedProposal:
 proposal:ApiCodingAgentProposal
@dataclass(slots=True)
class PreparedRun:
 run_id:str;active_workspace:Path;sandbox:Path;context:ApprovedBoundedContext;baseline:Any;active_before:Any;cancelled:threading.Event=field(default_factory=threading.Event);proposal:UntrustedProposal|None=None;review_id:str|None=None;task:asyncio.Task|None=None
@dataclass(frozen=True,slots=True)
class AdapterResult:
 run_id:str;review_id:str;changed_files:tuple[str,...];summary:str
class AgentAdapter(Protocol):
 @property
 def descriptor(self)->AgentAdapterDescriptor:...
 def readiness(self)->AdapterReadiness:...
 def prepare_run(self,*,run_id:str,workspace:Path,context:ApprovedBoundedContext)->PreparedRun:...
 async def execute_turn(self,run:PreparedRun,*,timeout_seconds:float)->UntrustedProposal:...
 def cancel(self,run:PreparedRun)->None:...
 def collect_result(self,run:PreparedRun)->AdapterResult:...
 def safe_diagnostics(self)->dict[str,Any]:...
@dataclass(frozen=True,slots=True)
class AdapterTurnInput:
 run_id:str;context:ApprovedBoundedContext;sandbox:Path|None=None
ProposalSource=Callable[[AdapterTurnInput],ApiCodingAgentProposal|str|Awaitable[ApiCodingAgentProposal|str]]
class ContainedProposalAdapter:
 def __init__(self,descriptor:AgentAdapterDescriptor,*,sandboxes:BridgeSandboxService,reviews:BridgeDiffService,source:ProposalSource):self._descriptor=descriptor;self.sandboxes=sandboxes;self.reviews=reviews;self.source=source
 @property
 def descriptor(self):return self._descriptor
 def readiness(self):return self._descriptor.readiness
 def prepare_run(self,*,run_id:str,workspace:Path,context:ApprovedBoundedContext):
  if self.readiness() is not AdapterReadiness.READY:raise AgentAdapterError(AdapterFailure.DISABLED,self.descriptor.safe_reason or "adapter disabled")
  active=workspace.resolve();active_before=self.reviews.inspect_workspace(active);sandbox=self.sandboxes.create_sandbox(run_id=run_id,workspace_root=active);baseline=self.reviews.snapshot_workspace(sandbox)
  return PreparedRun(run_id,active,sandbox,context,baseline,active_before)
 async def execute_turn(self,run:PreparedRun,*,timeout_seconds:float):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"run cancelled")
  async def invoke():
   turn_input=AdapterTurnInput(run.run_id,run.context,run.sandbox if self.descriptor.local_cli else None)
   value=self.source(turn_input);value=await value if inspect.isawaitable(value) else value
   return parse_api_coding_agent_response(value) if isinstance(value,str) else value
  try:
   run.task=asyncio.create_task(invoke());proposal=await asyncio.wait_for(run.task,timeout_seconds)
  except asyncio.TimeoutError as exc:run.cancelled.set();raise AgentAdapterError(AdapterFailure.TIMED_OUT,"adapter turn timed out") from exc
  except asyncio.CancelledError as exc:run.cancelled.set();raise AgentAdapterError(AdapterFailure.CANCELLED,"run cancelled") from exc
  except ApiCodingAgentContractError as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"adapter proposal failed validation") from exc
  finally:run.task=None
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"run cancelled")
  if not isinstance(proposal,ApiCodingAgentProposal):raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"adapter returned an invalid proposal")
  size=sum(len((item.content or "").encode()) for item in proposal.files)
  if size>MAX_OUTPUT_BYTES:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"proposal exceeds output limit")
  run.proposal=UntrustedProposal(proposal);return run.proposal
 def cancel(self,run):
  run.cancelled.set()
  if run.task and not run.task.done():run.task.cancel()
 def collect_result(self,run):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"run cancelled")
  if not run.proposal:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"proposal not available")
  proposal=run.proposal.proposal;declared={x.path.casefold() for x in proposal.files}
  # Detect any provider-side sandbox mutation before ForgeX materializes proposals.
  if self.reviews.diff_snapshot(run.baseline,run.sandbox):raise AgentAdapterError(AdapterFailure.UNDECLARED_CHANGE,"adapter mutated sandbox before proposal collection")
  for operation in proposal.files:
   target=(run.sandbox/operation.path).resolve()
   try:target.relative_to(run.sandbox.resolve())
   except ValueError as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"proposal path escaped sandbox") from exc
   if operation.action=="delete":target.unlink(missing_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(operation.content or "",encoding="utf-8",newline="\n")
  changes=self.reviews.diff_snapshot(run.baseline,run.sandbox)
  if {x.path.casefold() for x in changes}!=declared:raise AgentAdapterError(AdapterFailure.UNDECLARED_CHANGE,"sandbox changes do not exactly match proposal")
  if _fingerprint(self.reviews.inspect_workspace(run.active_workspace))!=_fingerprint(run.active_before):raise AgentAdapterError(AdapterFailure.ACTIVE_WORKSPACE_CHANGED,"active workspace integrity check failed")
  review=self.reviews.create_review(provider_id=self.descriptor.adapter_id,workspace_root=run.sandbox,snapshot=run.baseline,artifact_source="agent_adapter",artifact_type="untrusted_proposal",artifact_metadata={"commands_suggested":len(proposal.commands_suggested),"authoritative_diff":"forgex"})
  run.review_id=review.review_id;return AdapterResult(run.run_id,review.review_id,tuple(x.path for x in changes),proposal.summary)
 def safe_diagnostics(self):return {"adapter":self.descriptor.adapter_id,"readiness":self.readiness().value,"local_cli":self.descriptor.local_cli,"managed_sandbox_required":True,"returns_untrusted_proposals":True,"active_workspace_mutation":False,"apply":False,"build":False,"flash":False,"monitor":False,"approve":False,"shell_execution":False}

def verified_template_adapter(*,sandboxes,reviews):return ContainedProposalAdapter(_descriptor("verified_template","Verified templates","verified_template",AdapterReadiness.READY,prod=True),sandboxes=sandboxes,reviews=reviews,source=_template_source)
def structured_api_adapter(*,sandboxes,reviews,source):return ContainedProposalAdapter(_descriptor("structured_api_coding","Structured API coding","structured_api",AdapterReadiness.READY),sandboxes=sandboxes,reviews=reviews,source=source)
def deterministic_fake_adapter(*,sandboxes,reviews):return ContainedProposalAdapter(_descriptor("deterministic_fake","Deterministic fake provider","fake",AdapterReadiness.READY),sandboxes=sandboxes,reviews=reviews,source=lambda turn:_proposal("Deterministic bounded proposal",{PRODUCT_SMOKE_PATH:PRODUCT_SMOKE_CONTENT}))
def disabled_cli_descriptors():return (_descriptor("codex_cli","Codex CLI","local_cli",AdapterReadiness.DISABLED,True,"Containment evidence has not passed."),_descriptor("agy_cli","AGY CLI","local_cli",AdapterReadiness.DISABLED,True,"Containment evidence has not passed."))
def _descriptor(i,n,k,r,cli=False,reason=None,prod=False):return AgentAdapterDescriptor(i,n,k,r,cli,True,True,frozenset(),reason,prod)
def _template_source(run):
 template=match_template(run.context.instruction)
 if not template:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"no verified template matched")
 return _proposal(f"Generate verified {template.display_name} template.",dict(template.files))
def _proposal(summary,files):
 raw={"schema_version":API_CODING_AGENT_SCHEMA_VERSION,"summary":summary,"files":[{"path":p,"action":"create_or_update","content":c} for p,c in files.items()],"commands_suggested":[],"risks":[],"next_steps":[]}
 import json;return parse_api_coding_agent_response(json.dumps(raw))
def _secret(value):return any(x in value.casefold().replace(" ","") for x in _SECRET)
def _fingerprint(snapshot):return tuple(sorted((p,f.hash) for p,f in snapshot.files.items()))

class AgentAdapterRegistry:
 def __init__(self,adapters=()):self._adapters={};[self.register(a) for a in adapters]
 def register(self,adapter):
  key=getattr(adapter,"adapter_id",None) or adapter.descriptor.adapter_id
  if key in self._adapters:raise ValueError("agent adapter already registered")
  self._adapters[key]=adapter
 def get(self,adapter_id):return self._adapters[adapter_id]
 def descriptors(self):return tuple(self._adapters[k].descriptor for k in sorted(self._adapters))
