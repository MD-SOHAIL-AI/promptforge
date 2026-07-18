import asyncio,hashlib,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.agent_runtime.agent_adapter import AdapterFailure,AdapterReadiness,AgentAdapterError,AgentAdapterRegistry,ApprovedBoundedContext
from backend.agent_runtime.codex_cli_adapter import CodexCliAgentAdapter
from backend.bridges.base import DetectedExecutable
from backend.bridges.codex_login import CodexLoginLaunchResult
from backend.bridges.codex_status import CodexAlignedStatus
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.sandbox_service import BridgeSandboxService
class Status:
 def __init__(self,installed=True,auth="signed_in"):self.installed,self.auth=installed,auth
 def status(self):return CodexAlignedStatus(codex_installed=self.installed,codex_version="codex-cli 1.2.3" if self.installed else None,auth_status=self.auth,oauth_bridge_ready=self.auth=="signed_in",status_command_available=True)
 def resolve_executable(self,env=None):return DetectedExecutable("codex","codex") if self.installed else None
class Login:
 def __init__(self):self.calls=[]
 def launch(self,**kw):self.calls.append(kw);return CodexLoginLaunchResult("CODEX_LOGIN_LAUNCHED" if kw["confirm_launch_codex_login"] else "CODEX_LOGIN_CONFIRMATION_REQUIRED",kw["confirm_launch_codex_login"])
def env(tmp_path,status=None,runner=None,login=None):
 workspace=tmp_path/"active";workspace.mkdir(parents=True,exist_ok=True);(workspace/"main.txt").write_text("active\n");adapter=CodexCliAgentAdapter(sandboxes=BridgeSandboxService(tmp_path/"sandboxes"),reviews=BridgeDiffService(),status=status or Status(),runner=runner,login=login or Login());return workspace,adapter
def successful_runner(content="proposal\n",path="proposal.txt",extra=None):
 async def run(**kw):
  sandbox=Path(kw["cwd"]);target=sandbox/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content if isinstance(content,bytes) else content.encode())
  if extra:extra(sandbox)
  digest=hashlib.sha256(target.read_bytes()).hexdigest();manifest={"summary":"Codex proposal","files":[{"path":path,"action":"create_or_update","sha256":digest}]};out=Path(kw["command"][kw["command"].index("--output-last-message")+1]);out.write_text(json.dumps(manifest));return 0,"",""
 return run

def test_installation_and_official_auth_states(tmp_path):
 for status,expected in ((Status(False),AdapterReadiness.UNAVAILABLE),(Status(True,"signed_out"),AdapterReadiness.BLOCKED),(Status(True,"unknown"),AdapterReadiness.BLOCKED),(Status(True,"signed_in"),AdapterReadiness.READY)):
  _,adapter=env(tmp_path/expected.value,status=status,runner=successful_runner());assert adapter.readiness() is expected
  diagnostics=adapter.safe_diagnostics();assert diagnostics["auth_files_read"] is False and diagnostics["tokens_read"] is False and diagnostics["production_eligible"] is False

def test_login_launch_remains_confirmation_gated(tmp_path):
 login=Login();_,adapter=env(tmp_path,login=login,runner=successful_runner());assert not adapter.launch_login(confirmed=False).launched;assert adapter.launch_login(confirmed=True).launched;assert login.calls==[{"confirm_launch_codex_login":False},{"confirm_launch_codex_login":True}]

@pytest.mark.asyncio
async def test_review_generation_in_disposable_context_only_sandbox(tmp_path):
 workspace,adapter=env(tmp_path,runner=successful_runner());run=adapter.prepare_run(run_id="run",workspace=workspace,context=ApprovedBoundedContext("make proposal",{"main.txt":"approved\n"}));assert (run.sandbox/"main.txt").read_text()=="approved\n"
 await adapter.execute_turn(run,timeout_seconds=2);result=adapter.collect_result(run);assert result.changed_files==("proposal.txt",) and (workspace/"main.txt").read_text()=="active\n" and not (workspace/"proposal.txt").exists();assert adapter.descriptor.production_eligible is False

def test_codex_registered_as_adapter_not_model_endpoint(tmp_path):
 _,adapter=env(tmp_path,runner=successful_runner());registry=AgentAdapterRegistry((adapter,));assert registry.get("codex_cli") is adapter and registry.descriptors()[0].kind=="local_cli"

@pytest.mark.asyncio
async def test_cancellation_interrupts_runner_and_orphan_cleanup(tmp_path):
 started=asyncio.Event()
 async def slow(**kw):started.set();await asyncio.sleep(30);return 0,"",""
 workspace,adapter=env(tmp_path,runner=slow);run=adapter.prepare_run(run_id="cancel",workspace=workspace,context=ApprovedBoundedContext("safe"));task=asyncio.create_task(adapter.execute_turn(run,timeout_seconds=60));await started.wait();adapter.cancel(run)
 with pytest.raises(AgentAdapterError) as exc:await task
 assert exc.value.code is AdapterFailure.CANCELLED
 killed=[];fake=SimpleNamespace(returncode=None,pid=12345);adapter._processes["orphan"]=fake;adapter._kill_tree=lambda process:killed.append(process.pid);orphan=SimpleNamespace(run_id="orphan",cancelled=SimpleNamespace(set=lambda:None),task=None);adapter.cancel(orphan);assert killed==[12345]

@pytest.mark.asyncio
async def test_undeclared_and_malicious_paths_fail_closed(tmp_path):
 workspace,adapter=env(tmp_path,runner=successful_runner(extra=lambda root:(root/"extra.txt").write_text("undeclared")));run=adapter.prepare_run(run_id="extra",workspace=workspace,context=ApprovedBoundedContext("safe"));await adapter.execute_turn(run,timeout_seconds=2)
 with pytest.raises(AgentAdapterError) as exc:adapter.collect_result(run)
 assert exc.value.code is AdapterFailure.UNDECLARED_CHANGE
 workspace2,adapter2=env(tmp_path/"path",runner=successful_runner(path="../escape.txt"));run2=adapter2.prepare_run(run_id="path",workspace=workspace2,context=ApprovedBoundedContext("safe"))
 with pytest.raises(AgentAdapterError):await adapter2.execute_turn(run2,timeout_seconds=2)

@pytest.mark.asyncio
async def test_secret_binary_and_protected_outputs_rejected(tmp_path):
 for name,content,path in (("secret","token=raw","proposal.txt"),("binary",b"\xff\x00","proposal.txt"),("protected","safe",".env")):
  workspace,adapter=env(tmp_path/name,runner=successful_runner(content=content,path=path));run=adapter.prepare_run(run_id=name,workspace=workspace,context=ApprovedBoundedContext("safe"))
  with pytest.raises(AgentAdapterError) as exc:await adapter.execute_turn(run,timeout_seconds=2)
  assert exc.value.code is AdapterFailure.UNSAFE_OUTPUT
