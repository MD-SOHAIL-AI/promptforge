"""Run browser regressions against the built standalone desktop renderer."""
import asyncio
import os
from pathlib import Path
import shutil
import socket
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
from backend.runtime.subprocess_mgr import ProcessConfig,SubprocessManager
from backend.runtime.process_owner import owned_operation


async def main():
    specs=sys.argv[1:] or ["production-security.spec.ts","recovery-controls.spec.ts","local-session-recovery.spec.ts"]
    allowance=600 if sys.argv[1:] else 160
    packaged=ROOT / "frontend/.next/standalone/frontend"
    shutil.copytree(ROOT / "frontend/.next/static",packaged / ".next/static",dirs_exist_ok=True)
    shutil.copytree(ROOT / "frontend/public",packaged / "public",dirs_exist_ok=True)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1",0))
        port=reservation.getsockname()[1]
    node=shutil.which("node")
    manager=SubprocessManager()
    url=f"http://127.0.0.1:{port}"
    async def ready():
        try:
            response=await asyncio.to_thread(urllib.request.urlopen,url,timeout=2)
            response.close()
            return True
        except OSError:
            return False
    server=asyncio.create_task(owned_operation("production-renderer-qualification",manager.run(ProcessConfig(
        args=[node,str(packaged / "server.js")],cwd=str(packaged),
        env={"PORT":str(port),"HOSTNAME":"127.0.0.1","NODE_ENV":"production",
             "NODE_OPTIONS":os.environ.get("NODE_OPTIONS","--max-old-space-size=512 --max-semi-space-size=1")},timeout_s=allowance+30))))
    try:
        for _ in range(120):
            if await ready():break
            if server.done():raise RuntimeError((await server).stderr_text)
            await asyncio.sleep(.25)
        result=await manager.run(ProcessConfig(args=[node,str(ROOT / "frontend/node_modules/playwright/cli.js"),
            "test","--config","frontend/playwright.config.ts",*specs],
            cwd=str(ROOT),env={"FORGEX_BROWSER_BASE_URL":url,"FORGEX_BROWSER_PRODUCTION_CSP":"1"},timeout_s=allowance))
        evidence=Path(os.environ.get("FORGEX_QUALIFICATION_OUTPUT", str(ROOT / "audit/2026-10-09/production-renderer-remediation.log")))
        evidence.parent.mkdir(parents=True,exist_ok=True)
        evidence.write_text(result.stdout_text + result.stderr_text,encoding="utf-8")
        print(result.stdout_text + result.stderr_text,flush=True)
        return result.returncode
    finally:
        server.cancel()
        await asyncio.gather(server,return_exceptions=True)
        await manager.cancel_owner("production-renderer-qualification")


if __name__=="__main__":
    raise SystemExit(asyncio.run(main()))
