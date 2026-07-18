"""Deterministic, reviewed templates for common embedded tasks."""

from __future__ import annotations

import re
from dataclasses import dataclass


COMPLEXITY_TERMS: tuple[str, ...] = (
    "advanced",
    "dashboard",
    "web dashboard",
    "web server",
    "web",
    "ota",
    "over the air",
    "wifi config",
    "config portal",
    "settings page",
    "multi page",
    "database",
    "auth",
    "authentication",
    "login",
    "sensor graph",
    "sensor graphs",
    "chart",
    "charts",
    "real time chart",
    "real time charts",
    "mqtt",
    "cloud",
    "firebase",
    "thingspeak",
    "blynk",
    "bluetooth",
    "ble",
    "spiffs",
    "littlefs",
    "file upload",
    "rest api",
    "api endpoint",
    "multi sensor",
    "scheduler",
    "rtos",
    "freertos",
    "task",
    "tasks",
    "interrupt",
)


@dataclass(frozen=True, slots=True)
class VerifiedTemplate:
    id: str
    display_name: str
    keywords: tuple[str, ...]
    files: tuple[tuple[str, str], ...]

    def matches(self, task: str) -> bool:
        return self.matches_keywords(task) and not complexity_terms(task)

    def matches_keywords(self, task: str) -> bool:
        text = _normalize(task)
        return all(_contains_term(text, keyword) for keyword in self.keywords)


@dataclass(frozen=True, slots=True)
class TemplateMatchDecision:
    template: VerifiedTemplate | None
    template_candidate: str | None = None
    template_match_confidence: str | None = None
    template_match_reason: str | None = None
    template_rejected_reason: str | None = None
    matched_complexity_terms: tuple[str, ...] = ()

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "source": "verified_template" if self.template is not None else None,
            "template_id": self.template.id if self.template is not None else None,
            "template_candidate": self.template_candidate,
            "template_match_confidence": self.template_match_confidence,
            "template_match_reason": self.template_match_reason,
            "template_rejected_reason": self.template_rejected_reason,
            "matched_complexity_terms": list(self.matched_complexity_terms),
        }


ESP32_INI = """[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\nmonitor_speed = 115200\n"""


TEMPLATES: tuple[VerifiedTemplate, ...] = (
    VerifiedTemplate("esp32_blink", "ESP32 blink", ("esp32", "blink"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", "#include <Arduino.h>\nconstexpr uint8_t LED_PIN = LED_BUILTIN;\nvoid setup(){ pinMode(LED_PIN, OUTPUT); Serial.begin(115200); }\nvoid loop(){ digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(500); }\n"),
    )),
    VerifiedTemplate("arduino_blink", "Arduino blink", ("arduino", "blink"), (
        ("blink.ino", "void setup(){ pinMode(LED_BUILTIN, OUTPUT); }\nvoid loop(){ digitalWrite(LED_BUILTIN, HIGH); delay(500); digitalWrite(LED_BUILTIN, LOW); delay(500); }\n"),
    )),
    VerifiedTemplate("platformio_minimal", "PlatformIO minimal project", ("platformio", "minimal"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", "#include <Arduino.h>\nvoid setup(){ Serial.begin(115200); }\nvoid loop(){ delay(1000); }\n"),
    )),
    VerifiedTemplate("serial_monitor", "Basic serial project", ("serial", "monitor"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", "#include <Arduino.h>\nvoid setup(){ Serial.begin(115200); }\nvoid loop(){ Serial.println(\"ForgeX serial test\"); delay(1000); }\n"),
    )),
    VerifiedTemplate("wifi_scan", "ESP32 WiFi scan", ("wifi", "scan"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", "#include <Arduino.h>\n#include <WiFi.h>\nvoid setup(){ Serial.begin(115200); WiFi.mode(WIFI_STA); WiFi.disconnect(); }\nvoid loop(){ int count=WiFi.scanNetworks(); Serial.printf(\"Networks: %d\\n\", count); for(int i=0;i<count;i++) Serial.printf(\"%s (%d dBm)\\n\", WiFi.SSID(i).c_str(), WiFi.RSSI(i)); WiFi.scanDelete(); delay(5000); }\n"),
    )),
    VerifiedTemplate("esp32_wifi_monitor", "ESP32 WiFi device monitor", ("esp32", "wifi", "monitor"), (
        ("platformio.ini", ESP32_INI),
        ("include/config.h", "#pragma once\nconstexpr char WIFI_SSID[] = \"YOUR_WIFI_SSID\";\nconstexpr char WIFI_PASSWORD[] = \"YOUR_WIFI_PASSWORD\";\nconstexpr char DEVICE_NAME[] = \"forgex-esp32-monitor\";\n"),
        ("src/main.cpp", "#include <Arduino.h>\n#include <WebServer.h>\n#include <WiFi.h>\n#include \"config.h\"\nWebServer server(80);\nString statusJson(){ return String(\"{\\\"device\\\":\\\"\")+DEVICE_NAME+\"\\\",\\\"ip\\\":\\\"\"+WiFi.localIP().toString()+\"\\\",\\\"rssi\\\":\"+WiFi.RSSI()+\",\\\"uptime_ms\\\":\"+millis()+\"}\"; }\nvoid setup(){ Serial.begin(115200); WiFi.setHostname(DEVICE_NAME); WiFi.begin(WIFI_SSID,WIFI_PASSWORD); while(WiFi.status()!=WL_CONNECTED){ delay(500); Serial.print('.'); } server.on(\"/api/status\",[](){ server.send(200,\"application/json\",statusJson()); }); server.on(\"/\",[](){ server.send(200,\"text/html\",\"<h1>ESP32 Device Monitor</h1><pre id=s></pre><script>setInterval(async()=>s.textContent=JSON.stringify(await(await fetch('/api/status')).json(),null,2),2000)</script>\"); }); server.begin(); Serial.println(WiFi.localIP()); }\nvoid loop(){ server.handleClient(); delay(2); }\n"),
        ("README.md", "# ESP32 WiFi Device Monitor\n\nSet WiFi credentials in `include/config.h`, build with `pio run`, and open the IP printed at 115200 baud.\n"),
    )),
    VerifiedTemplate("oled_test", "SSD1306 OLED test", ("oled", "test"), (
        ("platformio.ini", ESP32_INI + "lib_deps = adafruit/Adafruit SSD1306@^2.5.13\n"),
        ("src/main.cpp", "#include <Arduino.h>\n#include <Wire.h>\n#include <Adafruit_SSD1306.h>\nAdafruit_SSD1306 display(128,64,&Wire,-1);\nvoid setup(){ Serial.begin(115200); if(!display.begin(SSD1306_SWITCHCAPVCC,0x3C)){ Serial.println(\"OLED not found\"); return; } display.clearDisplay(); display.setTextColor(SSD1306_WHITE); display.setTextSize(2); display.setCursor(0,0); display.println(\"ForgeX\"); display.display(); }\nvoid loop(){ delay(1000); }\n"),
    )),
    VerifiedTemplate("sensor_read", "Analog sensor read", ("sensor", "read"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", "#include <Arduino.h>\nconstexpr uint8_t SENSOR_PIN=34;\nvoid setup(){ Serial.begin(115200); }\nvoid loop(){ Serial.printf(\"Sensor: %d\\n\", analogRead(SENSOR_PIN)); delay(500); }\n"),
    )),
)


def complexity_terms(task: str) -> tuple[str, ...]:
    text = _normalize(task)
    return tuple(term for term in COMPLEXITY_TERMS if _contains_term(text, term))


def match_template_decision(task: str) -> TemplateMatchDecision:
    candidates = [template for template in TEMPLATES if template.matches_keywords(task)]
    blockers = complexity_terms(task)
    candidate_id = candidates[0].id if len(candidates) == 1 else None
    if blockers:
        return TemplateMatchDecision(
            template=None,
            template_candidate=candidate_id,
            template_rejected_reason=f"complexity_terms: {', '.join(blockers)}",
            matched_complexity_terms=blockers,
        )
    if len(candidates) == 1:
        return TemplateMatchDecision(
            template=candidates[0],
            template_candidate=candidates[0].id,
            template_match_confidence="high",
            template_match_reason="simple verified template request",
        )
    reason = "multiple verified template candidates" if candidates else "no verified template candidate"
    return TemplateMatchDecision(template=None, template_rejected_reason=reason)


def match_template(task: str) -> VerifiedTemplate | None:
    return match_template_decision(task).template


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _contains_term(text: str, term: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None
