"""Deterministic, reviewed templates for common embedded tasks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedTemplate:
    id: str
    display_name: str
    keywords: tuple[str, ...]
    files: tuple[tuple[str, str], ...]

    def matches(self, task: str) -> bool:
        text = " ".join(task.casefold().split())
        return all(keyword in text for keyword in self.keywords)


ESP32_INI = """[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\nmonitor_speed = 115200\n"""

ESP32_SERIAL_LED_CPP = """#include <Arduino.h>

// External LED wiring:
// GPIO 2 -> 220-330 ohm resistor -> LED anode (+)
// LED cathode (-) -> GND
// Many ESP32 boards already provide an onboard LED on GPIO 2.

constexpr uint8_t LED_PIN = 2;

void printHelp() {
  Serial.println("Type ON or OFF, then press Enter.");
}

void setup() {
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  Serial.begin(115200);
  Serial.setTimeout(100);
  Serial.println("ESP32 Serial LED control ready.");
  printHelp();
}

void loop() {
  if (!Serial.available()) {
    delay(10);
    return;
  }

  String command = Serial.readStringUntil('\n');
  command.trim();
  command.toUpperCase();

  if (command == "ON") {
    digitalWrite(LED_PIN, HIGH);
    Serial.println("LED is ON");
  } else if (command == "OFF") {
    digitalWrite(LED_PIN, LOW);
    Serial.println("LED is OFF");
  } else if (!command.isEmpty()) {
    Serial.print("Unknown command: ");
    Serial.println(command);
    printHelp();
  }
}
"""

ESP32_DHT_OLED_INI = """[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\nmonitor_speed = 115200\nlib_deps =\n  adafruit/DHT sensor library@^1.4.6\n  adafruit/Adafruit Unified Sensor@^1.1.15\n  adafruit/Adafruit SSD1306@^2.5.13\n  adafruit/Adafruit GFX Library@^1.12.1\n"""

ESP32_ROOM_MONITOR_CPP = """#include <Arduino.h>\n#include <Wire.h>\n#include <DHT.h>\n#include <Adafruit_GFX.h>\n#include <Adafruit_SSD1306.h>\n\nconstexpr uint8_t DHT_PIN = 4;\nconstexpr uint8_t PIR_PIN = 27;\nconstexpr uint8_t LDR_PIN = 34;\nconstexpr uint8_t BUZZER_PIN = 25;\nconstexpr uint8_t RED_LED_PIN = 26;\nconstexpr uint8_t GREEN_LED_PIN = 33;\nconstexpr uint8_t BUTTON_PIN = 32;\nconstexpr uint8_t OLED_SDA_PIN = 21;\nconstexpr uint8_t OLED_SCL_PIN = 22;\nconstexpr uint8_t DHT_TYPE = DHT11;\nconstexpr int ALERT_TEMPERATURE_C = 30;\nconstexpr int ALERT_HUMIDITY_PERCENT = 80;\nconstexpr int DARK_THRESHOLD_PERCENT = 25;\n\nDHT dht(DHT_PIN, DHT_TYPE);\nAdafruit_SSD1306 display(128, 64, &Wire, -1);\nbool alarmMuted = false;\nunsigned long lastSampleMs = 0;\n\nint readLightPercent() {\n  int raw = analogRead(LDR_PIN);\n  return constrain(map(raw, 0, 4095, 0, 100), 0, 100);\n}\n\nvoid drawStatus(float temperature, float humidity, int lightPercent, bool motion, bool alert) {\n  display.clearDisplay();\n  display.setTextColor(SSD1306_WHITE);\n  display.setTextSize(1);\n  display.setCursor(0, 0);\n  display.println(\"ForgeX Room Monitor\");\n  display.printf(\"Temp: %.1f C\\n\", temperature);\n  display.printf(\"Hum : %.1f %%\\n\", humidity);\n  display.printf(\"Light: %d %%\\n\", lightPercent);\n  display.printf(\"Motion: %s\\n\", motion ? \"YES\" : \"no\");\n  display.printf(\"Alert : %s\\n\", alert ? \"ACTIVE\" : \"clear\");\n  display.display();\n}\n\nvoid setup() {\n  Serial.begin(115200);\n  pinMode(PIR_PIN, INPUT);\n  pinMode(BUZZER_PIN, OUTPUT);\n  pinMode(RED_LED_PIN, OUTPUT);\n  pinMode(GREEN_LED_PIN, OUTPUT);\n  pinMode(BUTTON_PIN, INPUT_PULLUP);\n  analogReadResolution(12);\n  dht.begin();\n  Wire.begin(OLED_SDA_PIN, OLED_SCL_PIN);\n  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {\n    Serial.println(\"SSD1306 OLED not detected at 0x3C\");\n  }\n  display.clearDisplay();\n  display.display();\n}\n\nvoid loop() {\n  if (digitalRead(BUTTON_PIN) == LOW) {\n    alarmMuted = !alarmMuted;\n    delay(250);\n  }\n\n  if (millis() - lastSampleMs < 2000) {\n    delay(20);\n    return;\n  }\n  lastSampleMs = millis();\n\n  float temperature = dht.readTemperature();\n  float humidity = dht.readHumidity();\n  int lightPercent = readLightPercent();\n  bool motion = digitalRead(PIR_PIN) == HIGH;\n  bool sensorOk = !isnan(temperature) && !isnan(humidity);\n  bool alert = !sensorOk || temperature >= ALERT_TEMPERATURE_C || humidity >= ALERT_HUMIDITY_PERCENT || lightPercent <= DARK_THRESHOLD_PERCENT || motion;\n\n  digitalWrite(RED_LED_PIN, alert ? HIGH : LOW);\n  digitalWrite(GREEN_LED_PIN, alert ? LOW : HIGH);\n  digitalWrite(BUZZER_PIN, alert && !alarmMuted ? HIGH : LOW);\n\n  Serial.printf(\"temp=%.1fC humidity=%.1f%% light=%d%% motion=%s alert=%s muted=%s\\n\",\n                temperature, humidity, lightPercent, motion ? \"yes\" : \"no\", alert ? \"yes\" : \"no\", alarmMuted ? \"yes\" : \"no\");\n  drawStatus(temperature, humidity, lightPercent, motion, alert);\n}\n"""

ESP32_WATER_MONITOR_CPP = """#include <Arduino.h>\n\nconstexpr uint8_t WATER_LEVEL_PIN = 34;\nconstexpr uint8_t FLOW_SENSOR_PIN = 27;\nconstexpr uint8_t TURBIDITY_PIN = 35;\nconstexpr uint8_t TDS_PIN = 32;\nconstexpr uint8_t STATUS_LED_PIN = 2;\nconstexpr float FLOW_PULSES_PER_LITER = 450.0f;\n\nvolatile uint32_t flowPulses = 0;\nunsigned long lastSampleMs = 0;\nfloat totalLiters = 0.0f;\n\nvoid IRAM_ATTR onFlowPulse() {\n  flowPulses++;\n}\n\nfloat percentFromAnalog(uint8_t pin) {\n  int raw = analogRead(pin);\n  return constrain((raw / 4095.0f) * 100.0f, 0.0f, 100.0f);\n}\n\nvoid setup() {\n  Serial.begin(115200);\n  pinMode(STATUS_LED_PIN, OUTPUT);\n  pinMode(FLOW_SENSOR_PIN, INPUT_PULLUP);\n  analogReadResolution(12);\n  attachInterrupt(digitalPinToInterrupt(FLOW_SENSOR_PIN), onFlowPulse, RISING);\n}\n\nvoid loop() {\n  if (millis() - lastSampleMs < 1000) {\n    delay(20);\n    return;\n  }\n  lastSampleMs = millis();\n\n  noInterrupts();\n  uint32_t pulses = flowPulses;\n  flowPulses = 0;\n  interrupts();\n\n  float litersThisSecond = pulses / FLOW_PULSES_PER_LITER;\n  totalLiters += litersThisSecond;\n  float flowLitersPerMinute = litersThisSecond * 60.0f;\n  float waterLevelPercent = percentFromAnalog(WATER_LEVEL_PIN);\n  float turbidityPercent = percentFromAnalog(TURBIDITY_PIN);\n  float tdsPercent = percentFromAnalog(TDS_PIN);\n  bool alert = waterLevelPercent < 15.0f || turbidityPercent > 70.0f || tdsPercent > 70.0f;\n\n  digitalWrite(STATUS_LED_PIN, alert ? HIGH : LOW);\n  Serial.printf(\"water_level=%.1f%% flow=%.2fL/min total=%.3fL turbidity=%.1f%% tds=%.1f%% alert=%s\\n\",\n                waterLevelPercent, flowLitersPerMinute, totalLiters, turbidityPercent, tdsPercent, alert ? \"yes\" : \"no\");\n}\n"""


TEMPLATES: tuple[VerifiedTemplate, ...] = (
    VerifiedTemplate("esp32_serial_led", "ESP32 Serial LED control", ("esp32", "led", "serial"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", ESP32_SERIAL_LED_CPP),
    )),
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
    VerifiedTemplate("esp32_room_monitor", "ESP32 room monitor", ("esp32", "temperature"), (
        ("platformio.ini", ESP32_DHT_OLED_INI),
        ("src/main.cpp", ESP32_ROOM_MONITOR_CPP),
        ("README.md", "# ESP32 Room Monitor\n\nPlatformIO Arduino firmware for a DHT11 temperature/humidity sensor, PIR motion sensor, LDR, buzzer, LEDs, push button, and SSD1306 OLED.\n\nBuild with `pio run` after PlatformIO is installed.\n"),
    )),
    VerifiedTemplate("esp32_water_monitor", "ESP32 water quality monitor", ("esp32", "water"), (
        ("platformio.ini", ESP32_INI),
        ("src/main.cpp", ESP32_WATER_MONITOR_CPP),
        ("README.md", "# ESP32 Water Monitor\n\nPlatformIO Arduino firmware for water level, flow, turbidity, and TDS-style analog monitoring. Calibrate sensor conversion constants before deployment.\n\nBuild with `pio run` after PlatformIO is installed.\n"),
    )),
)


def match_template(task: str) -> VerifiedTemplate | None:
    matches = [template for template in TEMPLATES if template.matches(task)]
    if not matches:
        return None
    # Focused templates intentionally overlap generic fallbacks. Prefer the
    # most specific reviewed template, but reject genuinely ambiguous ties.
    most_keywords = max(len(template.keywords) for template in matches)
    most_specific = [template for template in matches if len(template.keywords) == most_keywords]
    return most_specific[0] if len(most_specific) == 1 else None
