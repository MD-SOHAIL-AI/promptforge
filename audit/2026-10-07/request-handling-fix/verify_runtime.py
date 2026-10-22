"""Smoke-test an isolated backend using a read-only copy of the live model catalog."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import httpx
from backend.runtime.process_owner import owned_operation
from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager
from backend.runtime_contract import compute_backend_source_fingerprint


async def main():
    source = ROOT / ".promptforge/state/model-router.sqlite3"
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        payload = json.loads(connection.execute("SELECT payload FROM model_settings WHERE id=1").fetchone()[0])
    manager = SubprocessManager()
    with tempfile.TemporaryDirectory(prefix="forgex-catalog-smoke-") as directory:
        temporary = Path(directory)
        snapshot = temporary / "catalog.json"
        snapshot.write_text(json.dumps(payload), encoding="utf-8")
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        token = secrets.token_urlsafe(32)
        environment = dict(os.environ)
        environment.update(FORGEX_ROOT=str(temporary), FORGEX_DATA_DIR=str(temporary),
            FORGEX_LEGACY_DATA_DIR=str(temporary / "legacy"), PROMPTFORGE_ROOT=str(temporary),
            FORGEX_SETTINGS_PATH=str(temporary / "settings.json"), FORGEX_LIVE_LOG_PATH=str(temporary / "live.jsonl"),
            FORGEX_LOCAL_TOKEN=token, FORGEX_BACKEND_PORT=str(port), FORGEX_BACKEND_URL=f"http://127.0.0.1:{port}")
        environment.pop("FORGEX_MODEL_ROUTER_SETTINGS_PATH", None)
        code = "\n".join([
            "import json, uvicorn",
            "from pathlib import Path",
            "from backend.model_router import storage",
            "from backend.model_router.credentials import MemoryCredentialStore",
            "storage.default_credential_store = lambda: MemoryCredentialStore()",
            "from backend.model_router.sqlite_storage import SQLiteProviderSettingsStorage",
            "from backend.model_router.registry import ProviderRegistry",
            "from backend.model_router.router import ModelRouterService",
            "from backend.model_router.usage import UsageTracker",
            "from backend.api.app import create_app",
            f"settings = SQLiteProviderSettingsStorage(Path({str(temporary / 'models.sqlite3')!r}), credential_store=MemoryCredentialStore())",
            f"settings.write(json.loads(Path({str(snapshot)!r}).read_text(encoding='utf-8')))",
            f"router = ModelRouterService(registry=ProviderRegistry(settings), usage=UsageTracker(Path({str(temporary / 'usage.jsonl')!r})))",
            "app = create_app(model_router_service=router)",
            f"uvicorn.run(app, host='127.0.0.1', port={port}, access_log=False)",
        ])
        server = asyncio.create_task(owned_operation("request-handling-smoke", manager.run(ProcessConfig(
            args=[sys.executable, "-c", code], cwd=str(ROOT), env=environment, timeout_s=150))))
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                    headers={"Authorization": f"Bearer {token}"}, timeout=15) as http:
                health = None
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and not server.done():
                    try:
                        response = await http.get("/health")
                        response.raise_for_status()
                        health = response.json()
                        break
                    except (httpx.HTTPError, OSError):
                        await asyncio.sleep(0.25)
                assert health and health["status"] == "healthy" and health["engine_ready"]
                assert health["source_fingerprint"] == compute_backend_source_fingerprint(ROOT)
                async def measure(path):
                    started = time.perf_counter()
                    response = await http.get(path)
                    response.raise_for_status()
                    value = response.json()
                    return {"path": path, "seconds": round(time.perf_counter() - started, 4),
                        "count": value.get("count"), "source": value.get("source"), "status": response.status_code}
                measurements = await asyncio.gather(*[measure(path) for path in
                    ["/models/providers/openrouter/models", "/health"] * 3])
                measurements.append(await measure("/v2/models/catalog"))
                catalogs = [item for item in measurements if item["count"] is not None]
                assert all(item["count"] == 466 and item["source"] == "cache" for item in catalogs), catalogs
                assert max(item["seconds"] for item in measurements) < 5, measurements
                result = {"status": health["status"], "engine_ready": health["engine_ready"],
                    "source_fingerprint": health["source_fingerprint"], "production_state_access": "read-only catalog copy",
                    "generated_project_files_edited": False, "inference_requests_sent": 0,
                    "catalog_median_seconds": statistics.median(item["seconds"] for item in catalogs),
                    "measurements": measurements}
                Path(__file__).with_name("runtime-validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(result, indent=2), flush=True)
        finally:
            await manager.cancel_owner("request-handling-smoke")
            server.cancel()
            await asyncio.gather(server, return_exceptions=True)
            await manager.kill_all()


if __name__ == "__main__":
    asyncio.run(main())
