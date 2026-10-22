"""Start the patched backend in isolation and verify its real health endpoint."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
with tempfile.TemporaryDirectory(prefix="forgex-fixed-startup-") as temporary:
    isolated = Path(temporary)
    with socket.socket() as socket_probe:
        socket_probe.bind(("127.0.0.1", 0))
        port = socket_probe.getsockname()[1]
    environment = dict(os.environ)
    environment.update(FORGEX_ROOT=str(isolated), FORGEX_DATA_DIR=str(isolated),
        FORGEX_LEGACY_DATA_DIR=str(isolated / "legacy"), PROMPTFORGE_ROOT=str(isolated),
        FORGEX_SETTINGS_PATH=str(isolated / "settings.json"),
        FORGEX_MODEL_ROUTER_SETTINGS_PATH=str(isolated / "models.json"),
        FORGEX_LIVE_LOG_PATH=str(isolated / "live.jsonl"),
        FORGEX_LOCAL_TOKEN=secrets.token_urlsafe(32), FORGEX_BACKEND_PORT=str(port),
        FORGEX_BACKEND_URL=f"http://127.0.0.1:{port}")
    code = ("from backend.model_router import storage; "
        "from backend.model_router.credentials import MemoryCredentialStore; "
        "storage.default_credential_store = lambda: MemoryCredentialStore(); "
        "import uvicorn; "
        f"uvicorn.run('backend.api.app:create_app', factory=True, host='127.0.0.1', port={port}, access_log=False)")
    with (isolated / "startup.log").open("w", encoding="utf-8") as logs:
        process = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=environment,
            stdout=logs, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 60
            health = None
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                        health = json.load(response)
                    break
                except (urllib.error.URLError, TimeoutError):
                    time.sleep(0.25)
            assert health, "The isolated backend did not become ready within 60 seconds."
            assert health["status"] == "healthy" and health["engine_ready"] is True, health
            result = {"status": health["status"], "engine_ready": health["engine_ready"],
                "service": health["service"], "source_fingerprint": health["source_fingerprint"],
                "runtime_contract": health["runtime_contract"], "local_auth_required": health["local_auth_required"],
                "platformio_available": (health.get("platformio") or {}).get("available"),
                "production_state_used": False, "model_requests_sent": 0}
            Path(__file__).with_name("startup-validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result, indent=2))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
