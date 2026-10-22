"""Offline review probes. Run from the repository root; no provider or hardware calls."""
import asyncio, hashlib, json, os, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["LOCALAPPDATA"] = str(Path.cwd()/".cache"/"review-localappdata")
from backend.services.platformio_service import PlatformIOService
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.runtime.result import BuildResult,ResultStatus
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.services.project_service import ProjectService
from backend.contracts.generated_project import GeneratedProject, GeneratedFile
from backend.api.dependencies import resolve_project
from backend.api.errors import APIError

async def probe_artifact(root):
    project=root/"stage"; (project/"src").mkdir(parents=True)
    (project/"platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    (project/"src/main.cpp").write_text("void setup(){} void loop(){}")
    service=PlatformIOService(SubprocessManager(),artifact_store=root/"artifacts")
    async def compile_snapshot(project,**kwargs):
        binary=Path(project)/".pio/build/esp32dev/firmware.bin"
        binary.parent.mkdir(parents=True); binary.write_bytes(b"offline firmware fixture")
        return BuildResult(success=True,status=ResultStatus.SUCCESS,firmware_path=str(binary),message="compiled",metadata={"environment":"esp32dev"})
    service._build_unlocked=compile_snapshot
    async def progress(*args): pass
    loop=AutonomousForgeAgentLoop(workspace_root=project,planner=object(),platformio=service,progress=progress)
    async def preflight(*args,**kwargs): return None
    loop._preflight_dependencies=preflight
    observation=await loop._build_once("esp32dev")
    return {"compiler_success":observation["output"].get("status"),"agent_success":observation["output"].get("success"),"agent_build_passed":loop.build_passed,"saved_firmware_count":len(list((root/"artifacts").rglob("firmware.bin")))}

async def probe_recovery(root):
    service=ProjectService(root/"projects")
    project=GeneratedProject(project_id="recovery-probe",project_name="firmware",target_board="ESP32",framework="PlatformIO",files=(GeneratedFile("platformio.ini","[env:esp32dev]\nboard=esp32dev\n","ini"),GeneratedFile("src/main.cpp","void setup(){} void loop(){}","cpp")),created_at=datetime.now(timezone.utc),metadata={})
    await service.create_project(project)
    target=root/"projects/firmware";target.replace(root/"projects/.firmware.backup")
    visible=await service.list_projects()
    try: await resolve_project(service,"recovery-probe"); api="resolved"
    except APIError as exc:api=exc.code
    loaded=await service.load_project("firmware")
    return {"projects_visible_after_crash":len(visible),"api_resolution":api,"direct_named_load_recovers":loaded.project_id=="recovery-probe"}

async def probe_deadline():
    import backend.model_router.runtime as runtime_module
    from backend.model_router.runtime import ModelRuntime
    from backend.model_router.models import ModelRequest, RequestExecutionContext,ModelCapabilities
    from backend.model_router.resolver import ResolvedModel
    class Resolver:
        def resolve(self,request):return ResolvedModel("openai","model","openai-completions",ModelCapabilities())
    class Provider:
        async def generate(self,request):await asyncio.sleep(.10);return "completed"
    original=runtime_module.request_transport_policy
    runtime_module.request_transport_policy=lambda request:SimpleNamespace(total_seconds=.03)
    started=time.monotonic()
    try:
        result=await ModelRuntime(Resolver(),{"openai":Provider()}).generate(ModelRequest(prompt="offline",execution_context=RequestExecutionContext(deadline=started+.5,managed=False)))
        return {"request_timeout_seconds":.03,"parent_timeout_seconds":.5,"elapsed_seconds":round(time.monotonic()-started,3),"result":result}
    finally:runtime_module.request_transport_policy=original

async def main():
    with tempfile.TemporaryDirectory(prefix="forgex-review-") as directory:
        root=Path(directory)
        output={"artifact_integration":await probe_artifact(root/"build"),"crash_recovery":await probe_recovery(root/"recovery"),"request_deadline":await probe_deadline()}
    print(json.dumps(output,indent=2))
    Path("reports/current-review-probes.json").write_text(json.dumps(output,indent=2)+"\n")
asyncio.run(main())
