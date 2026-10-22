"""Verify the restarted desktop's backend and preserve safe validation evidence."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from backend.runtime_contract import BACKEND_SOURCE_FINGERPRINT
from backend.services.firmware_manifest import verify_upload_set

audit = Path(__file__).parent
deadline = time.monotonic() + 90
last_error = "Backend not checked."
while time.monotonic() < deadline:
    try:
        with urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
            health = json.load(response)
        if (health.get("source_fingerprint") == BACKEND_SOURCE_FINGERPRINT
                and health.get("engine_ready")
                and health.get("platformio", {}).get("reason") == "available"):
            break
        last_error = "The backend has not yet loaded the updated source."
    except Exception as exc:
        last_error = type(exc).__name__
    time.sleep(2)
else:
    raise SystemExit(last_error)
assert health["status"] == "healthy" and health["local_auth_required"]
assert health["platformio"]["available"]
provider_response = None
while time.monotonic() < deadline:
    desktop_logs = list((ROOT / ".forgex/logs").glob("live-runtime.desktop-*.jsonl"))
    if desktop_logs:
        desktop_log = max(desktop_logs, key=lambda path: path.stat().st_mtime)
        for line in desktop_log.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (event.get("event") == "renderer.http.completed"
                    and event.get("path") == "/agent-runtime/providers"
                    and event.get("status") == 200):
                provider_response = event
                break
    if provider_response is not None:
        break
    time.sleep(2)
assert provider_response is not None, "The desktop provider-list request has not succeeded."
with urlopen("http://127.0.0.1:3000", timeout=20) as response:
    page = response.read().decode("utf-8")
    assert response.status == 200 and "ForgeX" in page
    frontend_status = response.status
context = json.loads((audit / "repair-context.json").read_text(encoding="utf-8"))
build = json.loads((audit / "build-4.json").read_text(encoding="utf-8"))
assert build["success"] and not build["process_timed_out"]
manifest = build["metadata"]["artifact_manifest"]
images = verify_upload_set(manifest, store=ROOT / ".promptforge/artifacts/firmware-manifests",
                           chip="esp32", environment="esp32dev")
original = json.loads((ROOT / ".promptforge/state/changesets" /
    (context["original_change_set_id"] + ".json")).read_text(encoding="utf-8"))
for file in original["changed_files"]:
    path = Path(context["original_staging_root"]) / file["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == file["new_hash"]
for name in ("README.md", "platformio.ini", "src/main.cpp"):
    assert (Path(context["workspace_root"]) / name).read_bytes() == (Path(context["repair_stage"]) / name).read_bytes()
state_path = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
with sqlite3.connect(state_path.as_uri() + "?mode=ro", uri=True) as conn:
    conn.execute("PRAGMA query_only=ON")
    old_status = conn.execute("SELECT status FROM agent_runs WHERE run_id=?",
        ("agent-run-bb29343bd869493b80f1c60a94b1076e",)).fetchone()[0]
assert old_status == "failed"
evidence = {"backend_status": health["status"], "engine_ready": health["engine_ready"],
    "local_auth_required": health["local_auth_required"],
    "source_fingerprint": health["source_fingerprint"], "updated_source_loaded": True,
    "platformio": health["platformio"], "frontend_status": frontend_status,
    "desktop_provider_list_status": provider_response["status"],
    "desktop_provider_list_duration_ms": provider_response["duration_ms"],
    "active_project": context["workspace_root"], "firmware_path": build["firmware_path"],
    "firmware_build_success": True, "firmware_build_duration_ms": build["duration_ms"],
    "warnings_count": build["warnings_count"], "sealed_upload_images": len(images),
    "manifest_sha256": manifest["sha256"], "original_draft_preserved": True,
    "original_run_status": old_status, "hardware_flashed": False}
(audit / "final-verification.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
print(json.dumps(evidence))
