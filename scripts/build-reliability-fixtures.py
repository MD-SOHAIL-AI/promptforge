"""Build target coverage from the board families currently exposed by ForgeX."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    ("esp32dev", "ESP32", "espressif32", ("arduino", "espidf"), "GPIO2"),
    ("esp32-c3-devkitm-1", "ESP32-C3", "espressif32", ("arduino", "espidf"), "GPIO4"),
    ("esp32-s3-devkitc-1", "ESP32-S3", "espressif32", ("arduino", "espidf"), "GPIO4"),
    ("uno", "Arduino Uno", "atmelavr", ("arduino",), "digital pin 13"),
    ("bluepill_f103c8", "STM32", "ststm32", ("arduino", "stm32cube"), "PC13"),
)


def build():
    cases = []
    for board, family, platform, frameworks, pin in TARGETS:
        for framework in frameworks:
            identity = board + ":" + framework
            cases.append({"id": identity, "target": board, "framework": framework,
                "board_type": family, "environment": "qualification", "platformio_board": board,
                "request": f"Create and build a minimal {family} project using PlatformIO platform {platform}, board {board}, "
                    f"framework {framework}, and environment qualification. Toggle an LED on {pin} every 1000 ms while keeping "
                    "the scheduler responsive. Use framework-appropriate APIs and document the wiring assumption. "
                    "Include platformio.ini and actual source implementations. Do not flash or open a serial port.",
                "follow_up": "Preserve the board, framework, pins and unrelated files. Change the interval to 750 ms and rebuild. Do not flash.",
                "historical": False, "safety_critical": True, "expected_status": "completed", "seed_files": {},
                "required_evidence": ["current_source", "current_firmware_build", "requirement_review"]})
    return {"schema_version": 1, "suite_version": "forgex.targets.v1", "repetitions": 3, "cases": cases}


if __name__ == "__main__":
    (ROOT / "tests/fixtures/agent-target-suite.json").write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
