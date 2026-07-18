"""Codex CLI AgentAdapter using official CLI-owned auth and managed sandboxes."""
from __future__ import annotations
import asyncio,hashlib,json,os,signal,stat,subprocess
from pathlib import Path
from typing import Any,Awaitable,Callable
from .agent_adapter import (AdapterFailure,AdapterReadiness,AdapterResult,AgentAdapterDescriptor,AgentAdapterError,ApprovedBoundedContext,PreparedRun,UntrustedProposal,_fingerprint)
from .api_agent_contracts import API_CODING_AGENT_SCHEMA_VERSION,parse_api_coding_agent_response
from backend.connection_registry import AuthState, ConnectionRegistry
from backend.bridges.codex_login import CodexLoginService
from backend.bridges.codex_status import CodexStatusService,build_codex_safe_user_env
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.sandbox_service import BridgeSandboxService

MAX_OUTPUT=64*1024;MAX_FILES=20;MAX_TOTAL=512*1024
Runner=Callable[...,Awaitable[tuple[int,str,str]]]
class CodexCliAgentAdapter:
 def __init__(self,*,sandboxes:BridgeSandboxService,reviews:BridgeDiffService,status:CodexStatusService|None=None,login:CodexLoginService|None=None,runner:Runner|None=None,connections:ConnectionRegistry|None=None):
  self.sandboxes,self.reviews=sandboxes,reviews;self.status=status or CodexStatusService();self.login=login or CodexLoginService(status_service=self.status);self.runner=runner or self._run_process;self.connections=connections;self._processes:dict[str,asyncio.subprocess.Process]={}
 @property
 def descriptor(self):return AgentAdapterDescriptor("codex_cli","Codex CLI","local_cli",self.readiness(),True,True,True,frozenset(),"Production eligibility remains disabled pending containment and parity evidence.",False)
 def readiness(self):
  if self.connections is not None:
   record=self.connections.refresh_status(self.connections.CODEX_ID)
   if not record.detected:return AdapterReadiness.UNAVAILABLE
   return AdapterReadiness.READY if record.auth_state is AuthState.AUTHENTICATED and record.connection_ready else AdapterReadiness.BLOCKED
  s=self.status.status()
  if not s.codex_installed:return AdapterReadiness.UNAVAILABLE
  return AdapterReadiness.READY if s.auth_status=="signed_in" and s.oauth_bridge_ready else AdapterReadiness.BLOCKED
 def launch_login(self,*,confirmed:bool):return self.login.launch(confirm_launch_codex_login=confirmed)
 def prepare_run(self,*,run_id:str,workspace:Path,context:ApprovedBoundedContext):
  if self.readiness() is not AdapterReadiness.READY:raise AgentAdapterError(AdapterFailure.DISABLED,"Codex CLI is not installed and officially signed in")
  active=workspace.resolve();active_before=self.reviews.inspect_workspace(active);base=self.sandboxes.sandbox_root.resolve();sandbox=(base/run_id).resolve()
  try:sandbox.relative_to(base)
  except ValueError as exc:raise AgentAdapterError(AdapterFailure.INVALID_CONTEXT,"sandbox escaped managed root") from exc
  sandbox.mkdir(parents=True,exist_ok=False)
  for relative,content in context.files.items():
   target=(sandbox/relative).resolve();target.relative_to(sandbox);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding="utf-8",newline="\n")
  baseline=self.reviews.snapshot_workspace(sandbox);return PreparedRun(run_id,active,sandbox,context,baseline,active_before)
 async def execute_turn(self,run:PreparedRun,*,timeout_seconds:float):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"Codex run cancelled")
  if self.connections is not None:
   record=self.connections.refresh_status(self.connections.CODEX_ID)
   if record.auth_state is not AuthState.AUTHENTICATED or not record.connection_ready:raise AgentAdapterError(AdapterFailure.DISABLED,"Official Codex status is not signed in")
  else:
   status=self.status.status()
   if status.auth_status!="signed_in" or not status.oauth_bridge_ready:raise AgentAdapterError(AdapterFailure.DISABLED,"Official Codex status is not signed in")
  executable=self.status.resolve_executable(build_codex_safe_user_env())
  if executable is None:raise AgentAdapterError(AdapterFailure.DISABLED,"Codex executable is unavailable")
  control=(self.sandboxes.sandbox_root/".codex-control"/run.run_id).resolve();control.mkdir(parents=True,exist_ok=False);result_path=control/"manifest.json"
  command=[executable.path,*executable.prefix_args,"--ask-for-approval","never","exec","--sandbox","workspace-write","--cd",str(run.sandbox),"--skip-git-repo-check","--output-last-message",str(result_path),"-"]
  instruction=self._instruction(run.context)
  try:
   run.task=asyncio.create_task(self.runner(command=command,cwd=run.sandbox,env=build_codex_safe_user_env(),input_text=instruction,timeout=timeout_seconds,run_id=run.run_id));code,stdout,stderr=await run.task
  except asyncio.CancelledError as exc:run.cancelled.set();raise AgentAdapterError(AdapterFailure.CANCELLED,"Codex run cancelled") from exc
  except asyncio.TimeoutError as exc:run.cancelled.set();raise AgentAdapterError(AdapterFailure.TIMED_OUT,"Codex run timed out") from exc
  finally:run.task=None
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"Codex run cancelled")
  if code!=0:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex CLI run failed")
  if len(stdout.encode())>MAX_OUTPUT or len(stderr.encode())>MAX_OUTPUT:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"Codex process output exceeded limit")
  try:
   if result_path.stat().st_size>MAX_OUTPUT:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"Codex manifest exceeded limit")
   manifest=json.loads(result_path.read_text(encoding="utf-8"))
  except (OSError,UnicodeError,json.JSONDecodeError) as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex manifest is invalid") from exc
  self._verify_containment(run.sandbox);proposal=self._proposal_from_manifest(run,manifest);run.proposal=UntrustedProposal(proposal);return run.proposal
 def cancel(self,run):
  run.cancelled.set()
  if run.task and not run.task.done():run.task.cancel()
  process=self._processes.get(run.run_id)
  if process and process.returncode is None:self._kill_tree(process)
 def collect_result(self,run):
  if run.cancelled.is_set():raise AgentAdapterError(AdapterFailure.CANCELLED,"Codex run cancelled")
  if not run.proposal:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex proposal unavailable")
  self._verify_containment(run.sandbox);changes=self.reviews.diff_snapshot(run.baseline,run.sandbox);declared={f.path.casefold() for f in run.proposal.proposal.files}
  if {c.path.casefold() for c in changes}!=declared:raise AgentAdapterError(AdapterFailure.UNDECLARED_CHANGE,"Codex made undeclared changes")
  if _fingerprint(self.reviews.inspect_workspace(run.active_workspace))!=_fingerprint(run.active_before):raise AgentAdapterError(AdapterFailure.ACTIVE_WORKSPACE_CHANGED,"active workspace integrity failed")
  review=self.reviews.create_review(provider_id="codex_cli",workspace_root=run.sandbox,snapshot=run.baseline,artifact_source="codex_exec",artifact_type="untrusted_proposal",artifact_metadata={"auth":"cli_owned_session","production_eligible":False});run.review_id=review.review_id
  return AdapterResult(run.run_id,review.review_id,tuple(c.path for c in changes),run.proposal.proposal.summary)
 def safe_diagnostics(self):
  status=(self.connections.safe_diagnostics(self.connections.CODEX_ID) if self.connections is not None else self.status.status().to_safe_dict());return {**status,"adapter_id":"codex_cli","readiness":self.readiness().value,"auth_type":"cli_owned_session","managed_sandbox_only":True,"auth_files_read":False,"tokens_read":False,"production_eligible":False,"review_only":True}
 def _proposal_from_manifest(self,run,manifest):
  if not isinstance(manifest,dict) or set(manifest)!={"summary","files"} or not isinstance(manifest["files"],list) or len(manifest["files"])>MAX_FILES:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex manifest contract invalid")
  operations=[];total=0
  for item in manifest["files"]:
   if not isinstance(item,dict) or set(item)!={"path","action","sha256"}:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex file declaration invalid")
   path,action,digest=item["path"],item["action"],item["sha256"]
   target=(run.sandbox/path).resolve()
   try:target.relative_to(run.sandbox.resolve())
   except (ValueError,TypeError) as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex path escaped sandbox") from exc
   if action=="delete":content=None;actual=hashlib.sha256(b"").hexdigest()
   else:
    try:data=target.read_bytes();content=data.decode("utf-8")
    except (OSError,UnicodeError) as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex output is binary or unreadable") from exc
    if "\x00" in content or _secret(content):raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex output contains binary or secret material")
    total+=len(data);actual=hashlib.sha256(data).hexdigest()
   if digest!=actual:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex file hash mismatch")
   operations.append({"path":path,"action":"delete" if action=="delete" else "create_or_update","content":content} if action!="delete" else {"path":path,"action":"delete"})
  if total>MAX_TOTAL:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"Codex files exceeded total limit")
  raw=json.dumps({"schema_version":API_CODING_AGENT_SCHEMA_VERSION,"summary":manifest["summary"],"files":operations,"commands_suggested":[],"risks":[],"next_steps":[]})
  try:return parse_api_coding_agent_response(raw,allow_delete=True)
  except Exception as exc:raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex proposal failed validation") from exc
 def _verify_containment(self,root):
  root=root.resolve();count=0;total=0
  for current,dirs,files in os.walk(root,followlinks=False):
   for name in dirs+files:
    path=Path(current)/name;info=path.lstat();attrs=getattr(info,"st_file_attributes",0)
    if path.is_symlink() or attrs&getattr(stat,"FILE_ATTRIBUTE_REPARSE_POINT",0):raise AgentAdapterError(AdapterFailure.UNSAFE_OUTPUT,"Codex output contains a reparse escape")
    path.resolve().relative_to(root)
   for name in files:
    count+=1;total+=(Path(current)/name).stat().st_size
    if count>40:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"Codex sandbox file count exceeded limit")
    if count>MAX_FILES+len(run_files:=[]):pass
   if total>MAX_TOTAL:raise AgentAdapterError(AdapterFailure.OUTPUT_LIMIT,"Codex sandbox output exceeded limit")
 @staticmethod
 def _instruction(context):return "Work only in the provided ForgeX sandbox. Do not run build, flash, monitor, or approval commands. Make bounded file edits, then return ONLY JSON: {\"summary\":string,\"files\":[{\"path\":string,\"action\":\"create_or_update\"|\"delete\",\"sha256\":lowercase_sha256}]}. Request: "+context.instruction
 async def _run_process(self,*,command,cwd,env,input_text,timeout,run_id):
  kwargs={"cwd":str(cwd),"env":dict(env),"stdin":asyncio.subprocess.PIPE,"stdout":asyncio.subprocess.PIPE,"stderr":asyncio.subprocess.PIPE}
  if os.name=="nt":kwargs["creationflags"]=subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_NO_WINDOW
  else:kwargs["start_new_session"]=True
  process=await asyncio.create_subprocess_exec(*command,**kwargs);self._processes[run_id]=process
  async def drain(stream):
   kept=bytearray();total=0
   while True:
    chunk=await stream.read(8192)
    if not chunk:break
    total+=len(chunk)
    if len(kept)<=MAX_OUTPUT:kept.extend(chunk[:MAX_OUTPUT+1-len(kept)])
   return bytes(kept),total
  try:
   process.stdin.write(input_text.encode());await process.stdin.drain();process.stdin.close()
   stdout_task=asyncio.create_task(drain(process.stdout));stderr_task=asyncio.create_task(drain(process.stderr))
   await asyncio.wait_for(process.wait(),timeout=timeout);(stdout,stdout_total),(stderr,stderr_total)=await asyncio.gather(stdout_task,stderr_task)
   if stdout_total>MAX_OUTPUT or stderr_total>MAX_OUTPUT:return process.returncode,"x"*(MAX_OUTPUT+1),""
   return process.returncode,stdout.decode(errors="replace"),stderr.decode(errors="replace")
  except (asyncio.TimeoutError,asyncio.CancelledError):
   self._kill_tree(process);await process.wait()
   for task in (locals().get("stdout_task"),locals().get("stderr_task")):
    if task:task.cancel()
   raise
  finally:self._processes.pop(run_id,None)
 @staticmethod
 def _kill_tree(process):
  if process.returncode is not None:return
  if os.name=="nt":subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,timeout=5,shell=False)
  else:
   try:os.killpg(process.pid,signal.SIGKILL)
   except OSError:process.kill()
def _protected(value):
 parts={p.casefold() for p in Path(value).parts};name=Path(value).name.casefold();return bool(parts&{".git",".promptforge",".forgex",".ssh"}) or name in {".env",".env.local","id_rsa","id_ed25519"} or name.endswith((".pem",".key",".p12",".pfx"))
def _secret(value):
 folded=value.casefold();return any(x in folded for x in ("-----begin private key-----","password=","secret=","token=","api_key=","authorization: bearer"))
