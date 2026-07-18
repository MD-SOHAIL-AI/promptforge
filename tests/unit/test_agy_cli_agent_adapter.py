import asyncio
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.agent_runtime.agent_adapter import AdapterFailure,AdapterReadiness,AgentAdapterError,AgentAdapterRegistry,ApprovedBoundedContext
from backend.agent_runtime.agy_cli_adapter import AGYCliAgentAdapter,AGYExecutionMode
from backend.bridges.agy_scratch_project_import import AGYScratchProjectImportService
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.sandbox_service import BridgeSandboxService
class Detector:
 def __init__(self,installed=True,auth="authenticated",version="agy 1.0.0"):self.value=SimpleNamespace(installed=installed,auth_status=auth,version=version)
 def detect(self):return self.value
class Runner:
 def __init__(self,reviews,delay=False):self.reviews=reviews;self.runs={};self.cancelled=[];self.delay=delay
 def start_run_in_sandbox(self,*,run_id,sandbox_root,prompt,timeout_seconds,**kw):
  root=Path(sandbox_root);baseline=self.reviews.snapshot_workspace(root);run=SimpleNamespace(run_id=run_id,status="running",review_id=None);self.runs[run_id]=run
  if not self.delay:
   (root/"agy.txt").write_text("proposal\n");review=self.reviews.create_review(provider_id="agy_cli",workspace_root=root,snapshot=baseline);run.review_id=review.review_id;run.status="review_ready"
  return run
 def get_run(self,run_id):return self.runs[run_id]
 def cancel_run(self,run_id):self.cancelled.append(run_id);self.runs[run_id].status="cancelled";return self.runs[run_id]
class Importer:
 def __init__(self,reviews,root):self.reviews=reviews;self.root=Path(root);self.paths=[]
 def import_project(self,path,review_context=None):
  self.paths.append(path);source=Path(path).resolve();source.relative_to(self.root.resolve());sandbox=self.root.parent/"imported";sandbox.mkdir();baseline=self.reviews.snapshot_workspace(sandbox)
  for item in source.iterdir():(sandbox/item.name).write_bytes(item.read_bytes())
  review=self.reviews.create_review(provider_id="agy_cli",workspace_root=sandbox,snapshot=baseline);return SimpleNamespace(classification="AGY_SCRATCH_IMPORT_PASS",review_id=review.review_id)
def setup(tmp_path,*,detector=None,delay=False,enabled=True):
 active=tmp_path/"active";active.mkdir(parents=True);(active/"main.txt").write_text("active\n");reviews=BridgeDiffService();scratch=tmp_path/"scratch";scratch.mkdir();runner=Runner(reviews,delay);importer=Importer(reviews,scratch);adapter=AGYCliAgentAdapter(detector=detector or Detector(),runner=runner,importer=importer,sandboxes=BridgeSandboxService(tmp_path/"sandboxes"),reviews=reviews,managed_generation_root=tmp_path/"generation",enabled=enabled);return active,adapter,runner,importer,reviews,scratch

def test_detection_auth_enablement_health_and_production_are_separate(tmp_path):
 active,adapter,*_=setup(tmp_path,detector=Detector(auth="unknown"),enabled=False);status=adapter.status_dimensions();assert status["detected"] and status["auth_state"]=="auth_unknown" and not status["enabled"] and status["health"]=="ready" and not status["production_eligible"] and adapter.readiness() is AdapterReadiness.DISABLED
 _,missing,*_=setup(tmp_path/"missing",detector=Detector(False,"not_installed",None));assert missing.status_dimensions()["auth_state"]=="not_installed" and missing.readiness() is AdapterReadiness.UNAVAILABLE

def test_all_modes_require_explicit_or_managed_bound_paths(tmp_path):
 active,adapter,*rest=setup(tmp_path)
 with pytest.raises(AgentAdapterError):adapter.prepare_mode(run_id="g",workspace=active,context=ApprovedBoundedContext("safe"),mode=AGYExecutionMode.SCRATCH_GENERATION)
 with pytest.raises(AgentAdapterError):adapter.prepare_mode(run_id="g",workspace=active,context=ApprovedBoundedContext("safe"),mode=AGYExecutionMode.SCRATCH_GENERATION,bound_path=tmp_path/"outside")
 with pytest.raises(AgentAdapterError):adapter.prepare_mode(run_id="i",workspace=active,context=ApprovedBoundedContext("safe"),mode=AGYExecutionMode.EXPLICIT_SCRATCH_IMPORT)
 run=adapter.prepare_mode(run_id="g",workspace=active,context=ApprovedBoundedContext("safe",{"input.txt":"bounded"}),mode=AGYExecutionMode.SCRATCH_GENERATION,bound_path=tmp_path/"generation"/"precomputed");assert run.bound_path.name=="precomputed" and (run.bound_path/"input.txt").is_file()

@pytest.mark.asyncio
async def test_sandbox_execution_returns_review_only_and_preserves_active(tmp_path):
 active,adapter,runner,*_=setup(tmp_path);run=adapter.prepare_run(run_id="sandbox",workspace=active,context=ApprovedBoundedContext("safe",{"main.txt":"approved\n"}));await adapter.execute_turn(run,timeout_seconds=2);result=adapter.collect_result(run)
 assert result.review_id and result.changed_files==("agy.txt",) and (active/"main.txt").read_text()=="active\n";diagnostics=adapter.safe_diagnostics();assert diagnostics["apply"] is False and diagnostics["build"] is False and diagnostics["flash"] is False and diagnostics["monitor"] is False

def test_adapter_is_registered_as_agent_not_endpoint(tmp_path):
 _,adapter,*_=setup(tmp_path);registry=AgentAdapterRegistry((adapter,));assert registry.get("agy_cli") is adapter and adapter.descriptor.production_eligible is False

@pytest.mark.asyncio
async def test_explicit_scratch_import_uses_exact_selected_path(tmp_path):
 active,adapter,runner,importer,reviews,scratch=setup(tmp_path);selected=scratch/"selected";selected.mkdir();(selected/"main.cpp").write_text("int main(){}\n");newer=scratch/"newer";newer.mkdir();(newer/"wrong.cpp").write_text("wrong\n")
 run=adapter.prepare_mode(run_id="import",workspace=active,context=ApprovedBoundedContext("import selected"),mode=AGYExecutionMode.EXPLICIT_SCRATCH_IMPORT,bound_path=selected);await adapter.execute_turn(run,timeout_seconds=2);result=adapter.collect_result(run)
 assert importer.paths==[str(selected.resolve())] and result.changed_files==("main.cpp",) and "wrong.cpp" not in result.changed_files

@pytest.mark.asyncio
async def test_cancellation_cleanup_and_limits(tmp_path):
 active,adapter,runner,*_=setup(tmp_path,delay=True);run=adapter.prepare_run(run_id="cancel",workspace=active,context=ApprovedBoundedContext("safe"));task=asyncio.create_task(adapter.execute_turn(run,timeout_seconds=20));await asyncio.sleep(.1);adapter.cancel(run)
 with pytest.raises(AgentAdapterError) as exc:await task
 assert exc.value.code is AdapterFailure.CANCELLED and runner.cancelled==["cancel"]
 assert adapter.cleanup(run) and not run.bound_path.exists()
 with pytest.raises(AgentAdapterError):ApprovedBoundedContext("safe",{"large.txt":"x"*(32*1024+1)})
