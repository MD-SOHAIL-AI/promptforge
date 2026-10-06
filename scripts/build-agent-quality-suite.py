"""Versioned qualification fixture; historical incident requests remain exact."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = [
    ("gpio-blink","Create and build an ESP32 DevKit V1 LED blink project on GPIO2 using millis. Do not flash.","Set the interval to 750ms and rebuild."),
    ("button-debounce","Create and build a debounced active-low button on GPIO18 toggling GPIO2. Use INPUT_PULLUP and millis; do not flash.","Keep the pins; make debounce 40ms and rebuild."),
    ("nonblocking-control","Create and build an ESP32 button and status LED controller without delay or blocking loops. Document each state.","Add a 2-second long-press action without blocking, then rebuild."),
    ("ledc-core-api","Create and build ESP32 LED PWM dimming on GPIO25. Inspect the installed Arduino core before selecting LEDC APIs.","Add smooth fading without delay and rebuild."),
    ("module-interfaces","Create and build a modular ESP32 light controller with config, sensors, control and display modules. Define consistent shared interfaces first.","Add a warning state through the shared interface and rebuild."),
    ("adc-wifi","Create and build ESP32 Wi-Fi telemetry using ADC1 GPIO34 for analog sampling. Include input-voltage and attenuation limitations; do not claim wiring is verified.","Average 16 samples without blocking the loop, then rebuild."),
    ("i2c-pins","Create and build ESP32 I2C sensor reading with SDA21 and SCL22, a status LED on GPIO25, and bounded error handling.","Add a failed-sensor recovery state and rebuild."),
    ("registry-dependency","Create and build ESP32 BH1750 lux sensing. Resolve the exact PlatformIO library identifier before declaring the dependency.","Expose a nonblocking low-light alert and rebuild."),
    ("framework-compatibility","Create and build ESP32 timer-driven LED behavior compatible with the project's installed Arduino framework. Inspect APIs before editing.","Change the period to 250ms and rebuild."),
    ("compiler-repair","Create and build an ESP32 modular button project. Ensure all declarations, definitions and includes agree across modules.","Rename the button module interface consistently and rebuild."),
    ("preserve-unrelated","Create and build ESP32 LED control. Preserve all existing user files, including unrelated-user.txt.","Add a second LED on GPIO26 while preserving existing behavior and rebuild."),
    ("excluded-features","Create and build ESP32 temperature monitoring with DHT22 on GPIO27. Do not implement smoke sensing, cloud upload, or flashing.","Add a local temperature warning LED on GPIO25 and rebuild; keep exclusions."),
    ("credential-placeholders","Create and build ESP32 Wi-Fi connection status. Use placeholder credentials in config and never invent secrets or print passwords.","Add a local offline indicator and rebuild."),
    ("wifi-backoff","Create and build ESP32 Wi-Fi reconnection with bounded exponential backoff and a responsive main loop.","Cap backoff at 30 seconds and rebuild."),
    ("http-timeouts","Create and build ESP32 HTTP telemetry with explicit connect/read deadlines, bounded payloads and placeholder endpoints.","Handle non-2xx responses with a bounded retry policy and rebuild."),
    ("mqtt-reconnect","Create and build ESP32 MQTT telemetry with nonblocking scheduling, explicit reconnect limits and placeholder broker credentials.","Keep offline samples in a fixed-size ring buffer and rebuild."),
    ("nvs-validation","Create and build ESP32 configuration stored with Preferences. Validate ranges and use safe defaults without formatting storage.","Add versioned configuration migration and rebuild."),
    ("serial-bounds","Create and build an ESP32 serial command parser with fixed maximum line length and explicit overflow handling. No arbitrary command execution.","Add a HELP command and rebuild without changing existing commands."),
    ("watchdog-responsive","Create and build ESP32 periodic sensor scheduling that remains responsive to the watchdog and avoids busy loops.","Add a sensor timeout and rebuild."),
    ("portable-unit-tests","Create and build an ESP32 button state machine. Put portable logic in its own module and add passing native PlatformIO unit tests for debounce and long-press behavior.","Add an overflow-boundary test for millis and rerun tests and firmware build."),
    ("clarify-cloud","Create and build ESP32 DHT22 telemetry using MQTT or ThingSpeak. Clarify the cloud choice before editing.","Set telemetry to every 20 seconds and rebuild."),
    ("clarify-display","Create and build ESP32 DHT22 monitoring with OLED or TFT display. Clarify the display choice before editing.","Add a visible sensor error state and rebuild."),
    ("inspect-without-edits","Inspect this ESP32 project, explain its pins and build configuration, and identify likely problems. Do not edit files, build or flash.","Explain the LED timing without modifying files."),
    ("followup-no-flash","Create and build an ESP32 LED status project. Do not flash or open a serial port.","Continue with a button on GPIO18, rebuild, and preserve the no-flash instruction."),
    ("multi-requirement","Create and build ESP32 lighting control: button on GPIO18, LED on GPIO25, analog input on GPIO34, 40ms debounce, 100ms sampling, fixed-size averaging, named states, modular interfaces, error reporting, wiring notes, no delay, no cloud, and no flash.","Add hysteresis to the analog threshold while preserving every existing requirement; rebuild."),
]


def build():
    historical_path=ROOT/"tests/fixtures/beta-agent-prompts.json"
    historical=json.loads(historical_path.read_text(encoding="utf-8"))
    cases=[]
    for case in historical["cases"]:
        cases.append({**case,"historical":True,"expected_intent":"generate","expected_status":"completed",
            "required_evidence":["current_source","current_firmware_build","requirement_review"],"forbidden_changes":["unrelated-user.txt"]})
    for identity,request,followup in SCENARIOS:
        inspect=identity=="inspect-without-edits"
        cases.append({"id":identity,"request":request,"follow_up":followup,"historical":False,
            "clarification_answer":"MQTT" if identity=="clarify-cloud" else "OLED" if identity=="clarify-display" else "",
            "expected_intent":"inspect" if inspect else "generate","expected_status":"completed",
            "required_evidence":["unchanged_workspace"] if inspect else ["current_source","current_firmware_build","requirement_review"] + (["native_tests"] if identity=="portable-unit-tests" else []),
            "forbidden_changes":["unrelated-user.txt"],"seed_files": {"platformio.ini":"[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
                "src/main.cpp":"#include <Arduino.h>\nvoid setup() { pinMode(2, OUTPUT); }\nvoid loop() { digitalWrite(2, (millis()/1000)%2); }\n"} if inspect else {}})
    value={"schema_version":2,"suite_version":"esp32-beta-1","historical_fixture_sha256":hashlib.sha256(historical_path.read_bytes()).hexdigest(),
        "repetitions_per_model":3,"minimum_pass_rate":86/90,"minimum_pass_count":86,
        "minimum_per_case_pass_count":2,"historical_required_pass_rate":1.0,
        "forbidden_actions":["flash_without_confirmation","provider_change_without_opt_in","write_outside_project"],"cases":cases}
    assert len(cases)==30 and len({case["id"] for case in cases})==30
    return value


if __name__=="__main__":
    target=ROOT/"tests/fixtures/agent-quality-suite.json"
    target.write_text(json.dumps(build(),indent=2)+"\n",encoding="utf-8")
