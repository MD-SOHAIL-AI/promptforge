"""Bounded ESP32 inspection tools. Project content never grants authority."""
from __future__ import annotations

import configparser
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit, urljoin

DOCS = (
    ("ESP32 GPIO and pin configuration", "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/gpio.html"),
    ("ESP32 ADC and Wi-Fi", "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/adc_oneshot.html"),
    ("ESP32 Wi-Fi networking", "https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/network/esp_wifi.html"),
    ("PlatformIO project configuration dependencies environments", "https://docs.platformio.org/en/stable/projectconf/index.html"),
    ("PlatformIO native and device unit tests", "https://docs.platformio.org/en/stable/advanced/unit-testing/runner.html"),
    ("Arduino ESP32 I2C Wire", "https://docs.espressif.com/projects/arduino-esp32/en/latest/api/i2c.html"),
    ("Arduino ESP32 timer PWM LEDC", "https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ledc.html"),
)
DOMAINS = frozenset({"docs.espressif.com", "docs.platformio.org", "docs.arduino.cc"})


def _compiled_source_paths(executor):
    """Read existing compilation metadata without exposing compiler commands."""
    from backend.services.project_profile import inspect_project_profile
    recognized = False
    for root in dict.fromkeys((executor.write_root, executor.read_root)):
        try:
            profile = inspect_project_profile(root, environment=executor.environment)
        except (OSError, ValueError, configparser.Error):
            return set(), False
        if profile.adapter != "unknown":
            recognized = True
            if profile.selection_required:
                return set(), False
            if profile.compiler_context_available:
                return {unit.path for unit in profile.compilation_units}, True
    if recognized:
        return set(), False
    # Compatibility for source-only projects with a root compilation database.
    try:
        records = json.loads(executor._read_overlay_text("compile_commands.json"))
    except (OSError, ValueError, KeyError):
        return set(), False
    if not isinstance(records, list) or len(records) > 2000:
        return set(), False
    roots = (executor.write_root, executor.read_root)
    compiled = set()
    for entry in records:
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            continue
        source = Path(entry["file"])
        directory = Path(str(entry.get("directory") or roots[0]))
        source = (directory / source).resolve(strict=False) if not source.is_absolute() else source.resolve(strict=False)
        for root in roots:
            if source.is_relative_to(root):
                compiled.add(source.relative_to(root).as_posix())
                break
    return compiled, True


def public_doc_target(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in DOMAINS or parsed.port not in {None,443} or parsed.username or parsed.password:
        raise ValueError("DOCS_TARGET_NOT_ALLOWED")
    addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("DOCS_PRIVATE_TARGET_BLOCKED")
    return parsed, addresses


def fetch_document(url):
    # Pin each connection to a validated IP while preserving TLS hostname validation.
    deadline = time.monotonic() + 10
    for _ in range(3):
        if time.monotonic() >= deadline:
            raise TimeoutError("DOCS_DEADLINE_EXCEEDED")
        parsed, addresses = public_doc_target(url)
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=3)
        raw = socket.create_connection((addresses[0],443), timeout=3)
        try:
            connection.sock = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            connection.request("GET", target, headers={"User-Agent":"ForgeX-docs/1", "Accept":"text/html,text/plain", "Accept-Encoding":"identity"})
            response = connection.getresponse()
            if response.status in {301,302,303,307,308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200:
                raise ValueError("DOCS_HTTP_" + str(response.status))
            kind = response.getheader("Content-Type", "").lower()
            if not any(value in kind for value in ("text/html", "text/plain")):
                raise ValueError("DOCS_CONTENT_UNSUPPORTED")
            chunks = []
            size = 0
            while size <= 1_000_000:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("DOCS_DEADLINE_EXCEEDED")
                if connection.sock:
                    connection.sock.settimeout(min(3, remaining))
                chunk = response.read1(min(16384, 1_000_001-size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            data = b"".join(chunks)
            if len(data) > 1_000_000:
                raise ValueError("DOCS_RESPONSE_TOO_LARGE")
            text = data.decode("utf-8", errors="replace")
            text = re.sub(r"<(script|style)\b.*?</\1>", "", text, flags=re.S|re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            return {"url":url,"sha256":hashlib.sha256(data).hexdigest(),"content":re.sub(r"\s+"," ",text)[:16000],
                    "trust":"untrusted_documentation", "truncated":len(text)>16000}
        finally:
            connection.close()
            raw.close()
    raise ValueError("DOCS_TOO_MANY_REDIRECTS")


def local_tool(executor, tool, args):
    from .tool_contracts import ToolName
    if tool is ToolName.SEARCH_DOCS:
        tokens = set(re.findall(r"\w+", args["query"].lower()))
        ranked = sorted(DOCS, key=lambda item: -len(tokens & set(re.findall(r"\w+", item[0].lower()))))
        return {"results":[{"title":title,"url":url} for title,url in ranked[:5]], "scope":"curated_official_catalog"}
    if tool is ToolName.FETCH_DOCS:
        return fetch_document(args["url"])
    if tool is ToolName.INSPECT_SYMBOLS:
        symbol = args["symbol"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", symbol):
            raise ValueError("SYMBOL_INVALID")
        compiled, database_present = _compiled_source_paths(executor)
        matches = []
        scanned = 0
        for name in executor._overlay_files():
            if Path(name).suffix.lower() not in {".c",".cc",".cpp",".h",".hpp",".ino"}:
                continue
            scanned += 1
            if scanned > 80:
                return {"matches":matches,"lexical_only":True,"truncated":True,
                        "compile_database_present":database_present}
            for number,line in enumerate(executor._read_overlay_text(name).splitlines(),1):
                if re.search(r"\b" + re.escape(symbol) + r"\b", line):
                    matches.append({"path":name,"line":number,"text":line[:300],
                                    "compiled":name in compiled})
                if len(matches)>=100:
                    return {"matches":matches,"lexical_only":True,"truncated":True,
                            "compile_database_present":database_present}
        return {"matches":matches,"lexical_only":True,"truncated":False,
                "compile_database_present":database_present}
    # A bounded overlay snapshot ensures inspection includes staged changes.
    import tempfile
    from .embedded_context import inspect_embedded_project
    with tempfile.TemporaryDirectory(prefix="forgex-inspect-") as temporary:
        root = Path(temporary)
        count=0
        for name in executor._overlay_files():
            if name != "platformio.ini" and Path(name).suffix.lower() not in {".c",".cc",".cpp",".h",".hpp",".ino"}:
                continue
            count+=1
            if count>80:
                break
            content = executor._read_overlay_text(name)
            target = executor.policy.resolve_path(root,name,must_exist=False)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content,encoding="utf-8")
        graph = inspect_embedded_project(root, environment=executor.environment)
        if tool is ToolName.VALIDATE_HARDWARE_CONFIG:
            return {"board":graph.board,"findings":list(graph.warnings),"summary":graph.render(),
                    "hardware_verified":False,"coverage":"source_heuristics", "truncated":count>80}
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(root/"platformio.ini",encoding="utf-8")
            environments = [s[4:] for s in parser.sections() if s.startswith("env:")]
            return {"configuration_present":(root/"platformio.ini").is_file(),"environments":environments,
                    "board":graph.board,"framework":graph.framework,"source_files_inspected":min(count,80),
                    "warnings":list(graph.warnings),"execution_performed":False,
                    "optional_tools":"Host tool availability is reported separately."}
        except configparser.Error:
            return {"configuration_present":True,"valid":False,"error":"PLATFORMIO_CONFIG_INVALID","execution_performed":False}
