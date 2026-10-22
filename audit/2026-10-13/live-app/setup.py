"""Create an owned ForgeX UI qualification session without changing live state."""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
CASE = Path(__file__).resolve().parent
DATA = CASE / "data"
STATE = DATA / ".promptforge" / "state"
PROJECT = CASE / "ESP32_Smart_Monitor"
STATE.mkdir(parents=True, exist_ok=True)
PROJECT.mkdir(exist_ok=True)
live = ROOT / ".promptforge" / "state"
for name in ("model-router.sqlite3", "model-router.selections.sqlite3"):
    source_path = live / name
    if source_path.exists() and not (STATE / name).exists():
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(STATE / name) as target:
                source.backup(target)
with sqlite3.connect((live / "agent-orchestration.sqlite3").as_uri() + "?mode=ro", uri=True) as source:
    payload = json.loads(source.execute("SELECT payload_json FROM agent_runs WHERE run_id=?",
        ("agent-run-76179a5dbe284873a4539abe1ad591a2",)).fetchone()[0])
contract = payload["_recovery_context"]["active_workspace"]["task_contract"]
(CASE / "prompt.txt").write_text(contract["request"], encoding="utf-8")
token = secrets.token_urlsafe(48)
private = Path(tempfile.gettempdir()) / ("forgex-ui-session-" + uuid.uuid4().hex + ".json")
private.write_text(json.dumps({"token": token}), encoding="utf-8")
environment = os.environ.copy()
environment.update(FORGEX_ROOT=str(ROOT), FORGEX_DATA_DIR=str(DATA),
    FORGEX_SETTINGS_PATH=str(CASE / "settings.json"), FORGEX_LOCAL_TOKEN=token,
    FORGEX_BACKEND_PORT="8300", FORGEX_BACKEND_URL="http://127.0.0.1:8300", FORGEX_FRONTEND_PORT="3300",
    FORGEX_LIVE_LOG_PATH=str(CASE / "live-runtime.jsonl"), FORGEX_DESKTOP_LAUNCH_ID="solo-ui-qualification",
    FORGEX_PLATFORMIO_EXECUTABLE=str(ROOT / ".venv/Scripts/pio.exe"),
    FORGEX_NODE_EXECUTABLE="C:/Program Files/nodejs/node.exe", FORGEX_ENABLE_SAFE_AUTO_RECOVERY="0")
for key in ("FORGEX_LEGACY_DATA_DIR", "FORGEX_MODEL_ROUTER_SETTINGS_PATH"):
    environment.pop(key, None)
backend_log = open(CASE / "backend.log", "w", encoding="utf-8")
backend = subprocess.Popen([str(ROOT / ".venv/Scripts/python.exe"), "-B", "-m", "uvicorn",
    "backend.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "8300", "--no-access-log"],
    cwd=ROOT, env=environment, stdout=backend_log, stderr=subprocess.STDOUT,
    creationflags=subprocess.CREATE_NO_WINDOW)
frontend_env = environment.copy()
frontend_env.update(FORGEX_NEXT_DIST_DIR=".next-solo-ui", NODE_OPTIONS="--max-old-space-size=1024")
frontend_log = open(CASE / "frontend.log", "w", encoding="utf-8")
frontend = subprocess.Popen(["C:/Program Files/nodejs/node.exe", str(ROOT / "frontend/node_modules/next/dist/bin/next"),
    "dev", "--hostname", "127.0.0.1", "--port", "3300"], cwd=ROOT / "frontend", env=frontend_env,
    stdout=frontend_log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
launch = {"backend_pid": backend.pid, "frontend_pid": frontend.pid, "backend_url": "http://127.0.0.1:8300",
    "frontend_url": "http://127.0.0.1:3300", "project": str(PROJECT), "prompt": str(CASE / "prompt.txt"),
    "provider": payload["provider_id"], "model": payload["model_id"], "prompt_characters": len(contract["request"])}
(CASE / "launch.json").write_text(json.dumps(launch, indent=2), encoding="utf-8")
print(json.dumps({**launch, "private_session_path": str(private)}))
