"""Capture only redacted user requests from the read-only incident database."""
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.observability.diagnostics import redact
from backend.security import has_high_confidence_secret

CASES = {
    "smoke-exclusion": "agent-run-4f93f04ca8b94af5814b05ccc87579ce",
    "modular-monitor": "agent-run-17ce6070e9644965b60d63af5305c44d",
    "bh1750-dependency": "agent-run-3a90e849f0174bb5bc1098a82decd53d",
    "simple-lighting": "agent-run-b2f7e6b1e1cb40ae9959760f39cdf4ff",
    "cloud-monitor": "agent-run-5590272b933141238ef6035bed6a3306",
}

def capture():
    database = ROOT / ".promptforge/state/agent-orchestration.sqlite3"
    fixtures = []
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        for identity, run_id in CASES.items():
            row = connection.execute("SELECT payload_json FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise ValueError("INCIDENT_NOT_FOUND: " + identity)
            instruction = json.loads(row[0])["_recovery_context"]["instruction"]
            request = instruction.rsplit("Latest user request:\n", 1)[-1].strip()
            request = re.sub(r"[A-Za-z]:[\\/][^\r\n]+", "<workspace-path>", request)
            request = redact(request)
            if has_high_confidence_secret(request):
                raise ValueError("FIXTURE_REQUIRES_REDACTION: " + identity)
            fixtures.append({"id": identity, "request": request,
                "clarification_answer": "MQTT, OLED, DHT22, CCS811",
                "follow_up": "Improve comments in src/main.cpp. Preserve functionality and pin assignments. Build and verify the project."})
    target = ROOT / "tests/fixtures/beta-agent-prompts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"schema_version": 1, "cases": fixtures}, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(fixtures)} redacted regression requests")

if __name__ == "__main__":
    capture()
