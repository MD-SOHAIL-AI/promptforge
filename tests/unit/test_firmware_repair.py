from __future__ import annotations

from pathlib import Path

from backend.agent_runtime.firmware_repair import (
    repair_missing_arduino_core_include, repair_verified_cpp_build_errors,
)


def test_repairs_missing_arduino_include_with_framework_and_compiler_evidence(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nframework = arduino\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "main.cpp"
    source.write_text(
        "void setup() { Serial.begin(115200); pinMode(2, OUTPUT); }\nvoid loop() {}\n",
        encoding="utf-8",
    )
    result = repair_missing_arduino_core_include(
        tmp_path,
        {
            "diagnostics": (
                "src/main.cpp:1:16: error: 'Serial' was not declared in this scope\n"
                "src/main.cpp:1:38: error: 'pinMode' was not declared in this scope"
            )
        },
    )
    assert result is not None
    assert result.changed_paths == ("src/main.cpp",)
    assert source.read_text(encoding="utf-8").startswith("#include <Arduino.h>\n\n")


def test_does_not_guess_without_all_required_evidence(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text(
        "[env:native]\nplatform = native\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "main.cpp"
    original = "void setup() { Serial.begin(115200); }\n"
    source.write_text(original, encoding="utf-8")
    result = repair_missing_arduino_core_include(
        tmp_path,
        {"diagnostics": "src/main.cpp:1:16: error: 'Serial' was not declared in this scope"},
    )
    assert result is None
    assert source.read_text(encoding="utf-8") == original


def test_repair_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text(
        "[env:x]\nframework = arduino\n", encoding="utf-8"
    )
    source = tmp_path / "src" / "main.cpp"
    source.write_text("#include <Arduino.h>\nvoid setup(){ Serial.begin(1); }\n", encoding="utf-8")
    assert repair_missing_arduino_core_include(
        tmp_path,
        {"diagnostics": "src/main.cpp:2:15: error: 'Serial' was not declared in this scope"},
    ) is None


def test_repair_compiler_proven_header_types_and_system_collision(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text("[env:x]\nframework = arduino\n", encoding="utf-8")
    pins = tmp_path / "include" / "Pins.h"
    button = tmp_path / "include" / "ButtonManager.h"
    main = tmp_path / "src" / "main.cpp"
    pins.write_text("#pragma once\nconstexpr uint8_t OLED_ADDRESS = 0x3C;\n", encoding="utf-8")
    button.write_text("#pragma once\nbool lastButtonState = HIGH;\n", encoding="utf-8")
    main.write_text("#include <Arduino.h>\nSystemManager system;\nvoid setup(){ system.begin(); }\n", encoding="utf-8")
    evidence = {"diagnostics": (
        "include/Pins.h:2:11: error: 'uint8_t' does not name a type\n"
        "include/ButtonManager.h:2:24: error: 'HIGH' was not declared in this scope\n"
        "src/main.cpp:2:15: error: 'SystemManager system' redeclared as different kind of symbol\n"
        "stdlib.h:203:5: note: previous declaration 'int system(const char*)'\n"
    )}
    repair = repair_verified_cpp_build_errors(tmp_path, evidence)
    assert repair is not None
    assert set(repair.changed_paths) == {"include/Pins.h", "include/ButtonManager.h", "src/main.cpp"}
    assert pins.read_text(encoding="utf-8").startswith("#include <Arduino.h>")
    assert button.read_text(encoding="utf-8").startswith("#include <Arduino.h>")
    assert "SystemManager systemManagerInstance;" in main.read_text(encoding="utf-8")
    assert "systemManagerInstance.begin()" in main.read_text(encoding="utf-8")
    assert repair_verified_cpp_build_errors(tmp_path, evidence) is None


def test_repair_registry_verified_platformio_library_name(tmp_path: Path) -> None:
    ini = tmp_path / "platformio.ini"
    ini.write_text(
        "[env:default]\nframework = arduino\nlib_deps =\n"
        "    Adafruit DHT Sensor Library\n    Adafruit GFX Library\n",
        encoding="utf-8",
    )
    evidence = {
        "diagnostics": "include/SensorManager.h:6:10: fatal error: DHT.h: No such file or directory",
        "dependency_candidates": [
            {"query": "header:DHT.h", "package": "adafruit/DHT sensor library", "source": "platformio_registry"},
            {"query": "header:DHT.h", "package": "markruys/DHT", "source": "platformio_registry"},
            {"query": "Adafruit DHT Sensor Library", "package": "adafruit/DHT sensor library", "source": "platformio_registry"},
        ],
    }
    repair = repair_verified_cpp_build_errors(tmp_path, evidence)
    assert repair is not None
    assert repair.changed_paths == ("platformio.ini",)
    assert "    adafruit/DHT sensor library\n" in ini.read_text(encoding="utf-8")
    assert "    Adafruit GFX Library\n" in ini.read_text(encoding="utf-8")
    assert repair_verified_cpp_build_errors(tmp_path, evidence) is None


def test_repair_compiler_proven_missing_member_declaration(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header = tmp_path / "include" / "SystemManager.h"
    source = tmp_path / "src" / "SystemManager.cpp"
    header.write_text("class SystemManager {\npublic:\n    void update();\nprivate:\n    int state;\n};\n", encoding="utf-8")
    source.write_text("void SystemManager::printSystemStatus() { }\n", encoding="utf-8")
    evidence = {"diagnostics": (
        "src/SystemManager.cpp:1:6: error: no declaration matches 'void SystemManager::printSystemStatus()'\n"
        "include/SystemManager.h:1:7: note: 'class SystemManager' defined here\n"
    )}
    repair = repair_verified_cpp_build_errors(tmp_path, evidence)
    assert repair is not None
    assert repair.changed_paths == ("include/SystemManager.h",)
    assert "private:\n    void printSystemStatus();\n" in header.read_text(encoding="utf-8")
    assert repair_verified_cpp_build_errors(tmp_path, evidence) is None


def test_repair_versioned_unknown_package_from_registry(tmp_path: Path) -> None:
    ini = tmp_path / "platformio.ini"
    ini.write_text(
        "[env:default]\nlib_deps =\n    adafruit/Adafruit DHT Sensor Library @ ^1.4.0\n",
        encoding="utf-8",
    )
    evidence = {
        "diagnostics": "UnknownPackageError: Could not find the package with 'adafruit/Adafruit DHT Sensor Library @ ^1.4.0' requirements",
        "dependency_candidates": [
            {"query": "adafruit/Adafruit DHT Sensor Library @ ^1.4.0", "package": "adafruit/DHT sensor library", "source": "platformio_registry"},
        ],
    }
    repair = repair_verified_cpp_build_errors(tmp_path, evidence)
    assert repair is not None
    assert "adafruit/DHT sensor library @ ^1.4.0" in ini.read_text(encoding="utf-8")


def test_repair_compiler_proven_member_signature_and_empty_defaulted_constructor(tmp_path: Path) -> None:
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "include" / "AlertManager.h").write_text(
        "class AlertManager {\nprivate:\n    void checkMotionAlerts(uint32_t currentMillis);\n};\n",
        encoding="utf-8",
    )
    alert = tmp_path / "src" / "AlertManager.cpp"
    alert.write_text("void AlertManager::checkMotionAlerts() {\n    last = currentMillis;\n}\n", encoding="utf-8")
    (tmp_path / "include" / "AppState.h").write_text("class AppState {\npublic:\n    AppState() = default;\n};\n", encoding="utf-8")
    state = tmp_path / "src" / "AppState.cpp"
    state.write_text("#include \"AppState.h\"\nAppState::AppState() {\n    // already initialized\n}\n", encoding="utf-8")
    evidence = {"diagnostics": (
        "src/AlertManager.cpp:1:6: error: no declaration matches 'void AlertManager::checkMotionAlerts()'\n"
        "include/AlertManager.h:3:10: note: candidate is: 'void AlertManager::checkMotionAlerts(uint32_t)'\n"
        "src/AppState.cpp:2:1: error: definition of explicitly-defaulted 'AppState::AppState()'\n"
        "include/AppState.h:3:5: note: 'constexpr AppState::AppState()' explicitly defaulted here\n"
    )}
    repair = repair_verified_cpp_build_errors(tmp_path, evidence)
    assert repair is not None
    assert set(repair.changed_paths) == {"src/AlertManager.cpp", "src/AppState.cpp"}
    assert "checkMotionAlerts(uint32_t currentMillis)" in alert.read_text(encoding="utf-8")
    assert "AppState::AppState" not in state.read_text(encoding="utf-8")
