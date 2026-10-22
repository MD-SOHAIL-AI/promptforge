"""Real-provider ESP32 qualification through ForgeX's authenticated ASGI API.

Desktop Computer Use is unavailable. This probe does not claim GUI coverage.
It never requests flashing, uploads, device reset, or serial monitoring.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from backend.api.app import create_app

PROMPT = (
    "Create a new ESP32 Arduino PlatformIO project named esp32-button-debounce-demo "
    "using board esp32dev. Use GPIO4 as an INPUT_PULLUP button and GPIO2 as an LED. "
    "Debounce the button with a 35 ms millis-based state machine. Each stable press "
    "toggles the LED and increments a press counter. At Serial 115200, report each "
    "press and print uptime plus free heap every five seconds. Keep the loop "
    "nonblocking and do not use delay. Include pin wiring and build instructions "
    "in README.md. Compile the project, but do not flash, upload, reset a device, "
    "or open a serial monitor. Write files only within this new project."
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--model")
    args = parser.parse_args()
    report = {"verification_path": "authenticated in-process ForgeX API", "gui_verified": False}
    report_path = Path("reports/esp32-button-backend-result-2026-10-21.json")

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    def request(client, method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        body = response.json()
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: HTTP {response.status_code}: {body}")
        return body

    token = secrets.token_urlsafe(32)
    app = create_app(local_api_token_override=token)
    try:
        port = int(os.getenv("FORGEX_BACKEND_PORT", "8000"))
        with TestClient(app, base_url=f"http://127.0.0.1:{port}", cookies={"forgex_local_session": token}) as client:
            report["selection"] = request(client, "GET", "/models/selection")["selection"]
            providers = request(client, "GET", "/models/providers")["providers"]
            provider = next(item for item in providers if item["provider_id"] == "openrouter")
            report["provider"] = {key: provider.get(key) for key in ("provider_id", "configured", "enabled", "default_model")}
            catalogue = request(client, "GET", "/models/providers/openrouter/models?refresh=true")
            models = catalogue.get("models", [])
            free = [item for item in models if item.get("free") is True and item.get("available") is not False]
            report["free_models"] = [item.get("model_id") for item in free]
            save()
            print(json.dumps(report, indent=2), flush=True)
            if not args.execute:
                return
            if not args.model or args.model not in report["free_models"]:
                raise RuntimeError("An explicitly selected model from the current free catalogue is required")
            if not provider.get("configured") or not provider.get("enabled"):
                raise RuntimeError("OpenRouter is not configured and enabled")
            report["model_id"] = args.model
            root = Path.cwd() / "workspace/projects/esp32-button-debounce-demo"
            if root.exists():
                root = root.with_name(root.name + "-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            root.mkdir(parents=True, exist_ok=False)
            report["project_path"] = str(root)
            imported = request(client, "POST", "/projects/import", json={"path": str(root)})
            project_id = imported["project_id"]
            report["project_id"] = project_id
            request(client, "POST", f"/agent-runtime/projects/{project_id}/trust", json={"trusted": True})
            started = request(client, "POST", "/agent-runtime/runs", json={
                "project_id": project_id, "instruction": PROMPT,
                "provider_id": "openrouter", "model_id": args.model,
                "autonomy": "build_only", "board_type": "esp32dev",
                "environment": "esp32dev", "start_monitor_after_flash": False,
            })
            run_id = started["run"]["run_id"]
            report.update({"run_id": run_id, "prompt": PROMPT})
            save()
            print(json.dumps({"run_id": run_id, "project_path": str(root)}), flush=True)
            last_status = None
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                observed = request(client, "GET", f"/agent-runtime/runs/{run_id}")
                run = observed.get("run", observed)
                status = run.get("status")
                if status != last_status:
                    print(json.dumps({"status": status, "stage": run.get("current_stage")}), flush=True)
                    last_status = status
                report["run"] = run
                save()
                if status in {"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out", "paused_model_unavailable", "awaiting_flash_confirmation"}:
                    report["settled"] = True
                    save()
                    return
                time.sleep(3)
            request(client, "POST", f"/agent-runtime/runs/{run_id}/cancel")
            raise TimeoutError("Qualification exceeded fifteen minutes; cancellation requested")
    except Exception as exc:
        report["error"] = str(exc)
        save()
        raise


if __name__ == "__main__":
    main()
