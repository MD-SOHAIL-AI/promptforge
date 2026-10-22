"""Compile a local ESP32 fixture through the fixed snapshot -> agent boundary.
No model API, flash, upload, reset, or serial operation is invoked.
"""
import asyncio,json,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.services.platformio_service import PlatformIOService
from backend.runtime.subprocess_mgr import SubprocessManager

async def main():
    root=Path.cwd()/".cache/fixes-2026-10-21/real-build"
    workspace=root/"project"
    (workspace/"src").mkdir(parents=True,exist_ok=True)
    (workspace/"platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nmonitor_speed=115200\n")
    (workspace/"src/main.cpp").write_text("""#include <Arduino.h>
unsigned long last = 0;
unsigned long counter = 0;
bool led = false;
void setup() { Serial.begin(115200); pinMode(2, OUTPUT); }
void loop() {
  const unsigned long now = millis();
  if (now - last >= 1000) {
    last = now;
    led = !led;
    digitalWrite(2, led);
    Serial.printf("Counter: %lu, Free heap: %u\\n", ++counter, ESP.getFreeHeap());
  }
}
""")
    manager=SubprocessManager()
    service=PlatformIOService(manager,artifact_store=root/"artifacts",timeout_s=300)
    async def progress(kind,payload):
        if kind in ("BUILD_STARTED","BUILD_COMPLETED","BUILD_FAILED","BUILD_BLOCKED"):
            print(kind,payload.get("message",""),flush=True)
    loop=AutonomousForgeAgentLoop(workspace_root=workspace,planner=object(),platformio=service,
        progress=progress,subprocess_manager=manager,trusted_project=True,max_runtime_seconds=600)
    result=await loop._build_once("esp32dev")
    build=loop.last_build
    output={"agent_build_passed":loop.build_passed,"verification_refresh":loop._refresh_verification(),
        "build":{k:build.get(k) for k in ("success","status","message","exit_code","board","environment",
        "build_size_bytes","firmware_path","firmware_hash","elf_path","elf_sha256","source_snapshot_sha256","flash_ready","error_code")}}
    Path("reports/fixes-real-build.json").write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps(output,indent=2),flush=True)
    if not loop.build_passed:
        print(str(build.get("diagnostics",result))[-3000:],flush=True)
        raise SystemExit(1)
asyncio.run(main())
