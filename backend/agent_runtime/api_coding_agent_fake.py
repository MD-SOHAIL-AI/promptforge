"""Deterministic fake coding-agent provider for contract tests only."""

from __future__ import annotations

import json

from .api_agent_contracts import (
    API_CODING_AGENT_SCHEMA_VERSION,
    MAX_FILE_BYTES,
    ApiCodingAgentContext,
)


class FakeApiCodingAgentProvider:
    provider_id = "fake_api_coding_agent"
    model_id = "fake-api-coding-agent-v1"
    production_eligible = False

    def generate(self, prompt: str, context: ApiCodingAgentContext | None = None) -> str:
        del context
        mode = prompt.casefold()
        if "invalid json" in mode:
            return '{"schema_version": "forgex.api_coding_agent.v1",'

        path = "src/main.cpp"
        content = _BLINK_MAIN
        summary = "Create a basic ESP32 blink project."
        files: list[dict[str, str]] = [
            {"path": "platformio.ini", "action": "create_or_update", "content": _PLATFORMIO_INI},
            {"path": path, "action": "create_or_update", "content": content},
        ]
        if "unsafe path" in mode:
            files = [{"path": "../evil.txt", "action": "create_or_update", "content": "unsafe\n"}]
            summary = "Exercise unsafe path validation."
        elif "protected path" in mode:
            files = [{"path": ".env", "action": "create_or_update", "content": "SECRET=unsafe\n"}]
            summary = "Exercise protected path validation."
        elif "oversized" in mode:
            files = [{"path": "src/main.cpp", "action": "create_or_update", "content": "x" * (MAX_FILE_BYTES + 1)}]
            summary = "Exercise proposal size validation."

        return json.dumps({
            "schema_version": API_CODING_AGENT_SCHEMA_VERSION,
            "summary": summary,
            "files": files,
            "commands_suggested": [{"command": "pio run", "reason": "Build the PlatformIO project"}],
            "risks": ["Requires a supported ESP32 board configuration."],
            "next_steps": ["Review the generated files.", "Run build."],
        })


_PLATFORMIO_INI = """[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n"""
_BLINK_MAIN = """#include <Arduino.h>\n\nvoid setup() { pinMode(LED_BUILTIN, OUTPUT); }\n\nvoid loop() {\n  digitalWrite(LED_BUILTIN, HIGH);\n  delay(500);\n  digitalWrite(LED_BUILTIN, LOW);\n  delay(500);\n}\n"""
