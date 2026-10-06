"""Exercise the bundled backend, local authentication and restart persistence."""
from __future__ import annotations

import argparse
import hashlib
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.runtime.windows_job import WindowsJob


def smoke(root):
    root = Path(root).resolve(strict=True)
    interpreter = root / "python/python.exe"
    if not interpreter.is_file() or not root.joinpath("release-manifest.json").is_file():
        raise ValueError("PACKAGED_RUNTIME_MISSING")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="forgex-package-smoke-") as temporary:
        data = Path(temporary)
        env = {**os.environ, "FORGEX_ROOT": str(root), "FORGEX_DATA_DIR": str(data),
               "FORGEX_LEGACY_DATA_DIR": "", "FORGEX_DESKTOP": "1", "FORGEX_LOCAL_TOKEN": token,
               "FORGEX_SETTINGS_PATH": str(data / "settings.json"), "FORGEX_BACKEND_PORT": str(port),
               "FORGEX_PLATFORMIO_EXECUTABLE": str(root / "forgex-pio.py"),
               "PLATFORMIO_CORE_DIR": str(data / "platformio"), "FORGEX_LIVE_LOG_PATH": str(data / "runtime.jsonl")}
        def request(route, *, authenticated=True, payload=None):
            headers = {"Authorization": "Bearer " + token} if authenticated else {}
            encoded = json.dumps(payload).encode() if payload is not None else None
            if encoded is not None:
                headers["Content-Type"] = "application/json"
            result = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=encoded, headers=headers), timeout=3)
            with result:
                return json.load(result)
        for cycle in range(2):
            job = WindowsJob()
            args = [str(interpreter), "-B", "-m", "uvicorn", "backend.api.app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"]
            log = data / f"backend-{cycle}.log"
            with log.open("wb") as output:
                process = subprocess.Popen(job.wrap(args), cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
                try:
                    deadline = time.monotonic() + 40
                    while True:
                        try:
                            health = request("/health", authenticated=False)
                            if health.get("local_auth_required") is not True:
                                raise ValueError("PACKAGED_AUTH_DISABLED")
                            break
                        except (urllib.error.URLError, TimeoutError, ConnectionError):
                            if process.poll() is not None or time.monotonic() > deadline:
                                raise RuntimeError("PACKAGED_BACKEND_STARTUP_FAILED: " + log.read_text(errors="replace")[-4000:])
                            time.sleep(0.15)
                    try:
                        request("/settings", authenticated=False)
                        raise AssertionError("Unauthenticated settings request was accepted")
                    except urllib.error.HTTPError as denied:
                        assert denied.code == 401
                    settings = request("/settings")
                    assert isinstance(settings, dict)
                    provenance = request("/diagnostics/runtime")
                    assert provenance["packaged"] is True
                    assert provenance["resource_manifest_sha256"] == hashlib.sha256((root / "release-manifest.json").read_bytes()).hexdigest()
                    assert provenance["source_fingerprint"] and provenance["backend_instance_id"]
                    catalog = request("/agent-runtime/tools")
                    assert catalog["execution_authority"] == "python"
                    names = {tool["name"] for tool in catalog["tools"]}
                    assert {"read_file", "build_firmware", "inspect_symbols"} <= names
                    assert len(names) == len(catalog["tools"])
                    # A non-secret session is stored and then read after a full restart.
                    if cycle == 0:
                        project = data / "controller"
                        project.mkdir()
                        (project / "platformio.ini").write_text(
                            "[env:debug]\nplatform=espressif32\nframework=arduino\nboard=esp32dev\n"
                            "[env:release]\nplatform=espressif32\nframework=arduino\nboard=esp32dev\n",
                            encoding="utf-8")
                        (project / "src").mkdir()
                        (project / "src/main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
                        imported = request("/projects/import", payload={"path": str(project)})
                        project_id = imported["project_id"]
                        assert request(f"/projects/{project_id}/profile")["selection_required"] is True
                        response = request("/agent-runtime/sessions", payload={"kind": "chat", "title": "Packaged smoke"})
                        session_id = response["session"]["session_id"]
                    else:
                        response = request("/agent-runtime/sessions/" + session_id)
                        assert response["session"]["session_id"] == session_id
                    selected = request(f"/projects/{project_id}/profile?environment=release")
                    assert selected["selected_environment"] == "release"
                    assert selected["selection_required"] is False
                    assert {item["name"] for item in selected["environments"]} == {"debug", "release"}
                finally:
                    job.terminate()
                    job.close()
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
        assert data.joinpath(".promptforge/state").is_dir()
        print("Packaged backend passed: bundled imports, local authentication, per-user storage, tool catalog, selected-environment profiles and session persistence across restart")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path)
    arguments = parser.parse_args()
    root = arguments.root or Path(json.loads(REPO.joinpath(".forgex/release-stage.json").read_text())["stage"]) / "forgex"
    smoke(root)
