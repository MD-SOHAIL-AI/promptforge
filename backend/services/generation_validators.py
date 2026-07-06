"""Per-file validation helpers for chunked firmware generation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from ..contracts.generated_project import GeneratedFile

__all__ = [
    "strip_file_content",
    "validate_chunk_file",
]


def strip_file_content(raw: str, path: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, Mapping):
        content = payload.get("content")
        if isinstance(content, str):
            return content.strip("\r\n")
        files = payload.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, Mapping) and item.get("path") == path and isinstance(item.get("content"), str):
                    return str(item["content"]).strip("\r\n")
    fence = re.fullmatch(r"```(?:[a-zA-Z0-9_+-]+)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence is not None:
        return fence.group(1).strip("\r\n")
    return text.strip("\r\n")


def validate_chunk_file(path: str, content: str, *, advanced: bool = False) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        GeneratedFile(path=path, content=content)
    except ValueError as exc:
        errors.append(str(exc))
    if not content.strip():
        errors.append("file content is empty")
        return tuple(errors)

    normalized = content.casefold()
    forbidden = ("mqtt", "firebase", "blynk", "oled", "relay", "external sensor", "cdn.jsdelivr", "unpkg.com")
    for item in forbidden:
        if item in normalized:
            errors.append(f"forbidden feature detected: {item}")

    if path == "platformio.ini":
        if "[env:esp32dev]" not in normalized:
            errors.append("platformio.ini must define [env:esp32dev]")
        if "platform" not in normalized or "espressif32" not in normalized:
            errors.append("platformio.ini must use platform = espressif32")
        if "board" not in normalized or "esp32dev" not in normalized:
            errors.append("platformio.ini must use board = esp32dev")
        if "framework" not in normalized or "arduino" not in normalized:
            errors.append("platformio.ini must use framework = arduino")

    if path == "src/main.cpp":
        if "void setup" not in normalized:
            errors.append("src/main.cpp must define setup()")
        if "void loop" not in normalized:
            errors.append("src/main.cpp must define loop()")
        if _likely_truncated_cpp(content):
            errors.append("src/main.cpp appears truncated")
        if advanced:
            signals = {
                "WiFi.softAP": "wifi.softap",
                "WebServer": "webserver",
                "Preferences": "preferences",
                "FreeRTOS task": "xtaskcreate",
                "Serial": "serial",
                "/api/status": "/api/status",
                "/api/led/on": "/api/led/on",
                "/api/led/off": "/api/led/off",
            }
            for label, signal in signals.items():
                if signal not in normalized:
                    errors.append(f"advanced src/main.cpp missing {label}")

    if path == "README.md":
        required = ("overview", "features", "build", "upload", "serial", "api")
        for item in required:
            if item not in normalized:
                errors.append(f"README.md missing {item} section")
        if content.count("```") % 2:
            errors.append("README.md has an unclosed code fence")

    return tuple(dict.fromkeys(errors))


def _likely_truncated_cpp(content: str) -> bool:
    stripped = content.rstrip()
    if stripped.endswith((",", "+", "=", "(", "{", "[", "return")):
        return True
    balance = 0
    in_string = False
    escape = False
    for char in content:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            balance += 1
        elif char == "}":
            balance -= 1
    return in_string or balance != 0
