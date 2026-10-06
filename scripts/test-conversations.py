"""Run browser tests with an owned server and finite process-tree cleanup."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import socket
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.runtime.process_owner import owned_operation
from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager


def free_port():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def ready(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


async def main():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node is required for conversation tests")
    manager = SubprocessManager()
    frontend = ROOT / "frontend"
    server = None
    server_owner = "conversation-web-server"
    environment = {key: value for key, value in os.environ.items()
                   if key.startswith("FORGEX_BROWSER_") or key in {"CI", "DEBUG"}}
    url = environment.get("FORGEX_BROWSER_BASE_URL")
    generated_metadata = {}
    if not url:
        for name in ("next-env.d.ts", "tsconfig.json"):
            path = frontend / name
            generated_metadata[path] = path.read_bytes() if path.exists() else None
    try:
        if not url:
            assets = await manager.run(ProcessConfig(args=[node, "scripts/prepare-frontend-assets.mjs"],
                cwd=str(ROOT), timeout_s=60))
            if not assets.success:
                raise RuntimeError(assets.stderr_text or "Frontend asset preparation failed")
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            server_config = ProcessConfig(args=[node, str(frontend / "node_modules/next/dist/bin/next"),
                "dev", "--hostname", "127.0.0.1", "--port", str(port)], cwd=str(frontend),
                env={"FORGEX_NEXT_DIST_DIR": ".next-e2e", "NEXT_TELEMETRY_DISABLED": "1"}, timeout_s=720)
            server = asyncio.create_task(owned_operation(server_owner, manager.run(server_config)))
            expires = asyncio.get_running_loop().time() + 120
            while not await asyncio.to_thread(ready, url):
                if server.done():
                    result = await server
                    raise RuntimeError(result.stderr_text or "Conversation web server exited before readiness")
                if asyncio.get_running_loop().time() >= expires:
                    raise RuntimeError("Conversation web server readiness timed out")
                await asyncio.sleep(0.2)
            if server.done():
                raise RuntimeError("Conversation web server exited during readiness")
        environment["FORGEX_BROWSER_BASE_URL"] = url
        result = await owned_operation("conversation-tests", manager.run(ProcessConfig(args=[node,
            str(frontend / "node_modules/playwright/cli.js"), "test", "--config", "frontend/playwright.config.ts",
            *sys.argv[1:]], cwd=str(ROOT), env=environment, timeout_s=600)))
        print(result.stdout_text, end="", flush=True)
        print(result.stderr_text, end="", file=sys.stderr, flush=True)
        return result.returncode if not result.timed_out else 1
    finally:
        try:
            if server is not None:
                server.cancel()
                await asyncio.gather(server, return_exceptions=True)
                await manager.cancel_owner(server_owner)
        finally:
            for path, content in generated_metadata.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        raise SystemExit(asyncio.run(main()))
    except (OSError, RuntimeError) as error:
        print(f"Conversation test runner: {error}", file=sys.stderr)
        raise SystemExit(1)
