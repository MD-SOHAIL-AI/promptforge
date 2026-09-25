"""Deterministic embedded-project graph for Forge Agent context.

The graph intentionally stays local and conservative.  It extracts useful
firmware/hardware facts without pretending to replace a compiler, board data
sheet, or schematic parser.  Rules are emitted only where Forge can make a
reasonably strong inference from source plus PlatformIO configuration.
"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path

_PIN_PATTERNS = (
    re.compile(r"^\s*#define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:GPIO_NUM_)?(\d+)\b", re.M),
    re.compile(r"\b(?:const(?:expr)?\s+)?(?:int|uint8_t|gpio_num_t)\s+([A-Za-z_][A-Za-z0-9_]*PIN[A-Za-z0-9_]*)\s*=\s*(?:GPIO_NUM_)?(\d+)\b", re.I),
)
_PIN_MODE_RE = re.compile(
    r"\bpinMode\s*\(\s*([A-Za-z_][A-Za-z0-9_]*|\d+)\s*,\s*(OUTPUT|INPUT(?:_PULLUP|_PULLDOWN)?)\s*\)",
    re.I,
)
_OUTPUT_CALL_RE = re.compile(r"\b(?:digitalWrite|analogWrite|ledcAttach(?:Pin)?|dacWrite)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*|\d+)", re.I)
_ANALOG_READ_RE = re.compile(r"\banalogRead\s*\(\s*([A-Za-z_][A-Za-z0-9_]*|\d+)", re.I)
_I2C_ADDRESS_PATTERNS = (
    re.compile(r"\bbeginTransmission\s*\(\s*(0x[0-9A-Fa-f]{2})\s*\)"),
    re.compile(r"\bsetAddress\s*\(\s*(0x[0-9A-Fa-f]{2})\s*\)"),
    re.compile(r"\b(?:display|oled)\s*\.\s*begin\s*\([^;\n]*?\b(0x[0-9A-Fa-f]{2})\s*\)", re.I),
)

_CLASSIC_ESP32_INPUT_ONLY = frozenset(range(34, 40))
_CLASSIC_ESP32_FLASH_PINS = frozenset(range(6, 12))
_CLASSIC_ESP32_STRAPPING = frozenset({0, 2, 5, 12, 15})
_CLASSIC_ESP32_ADC2 = frozenset({0, 2, 4, 12, 13, 14, 15, 25, 26, 27})


@dataclass(frozen=True, slots=True)
class HardwarePin:
    symbol: str
    pin: int
    source: str
    mode: str | None = None


@dataclass(frozen=True, slots=True)
class I2CAddress:
    address: int
    source: str

    @property
    def hex(self) -> str:
        return f"0x{self.address:02X}"


@dataclass(frozen=True, slots=True)
class EmbeddedProjectGraph:
    board: str | None = None
    framework: str | None = None
    environments: tuple[str, ...] = ()
    pins: tuple[HardwarePin, ...] = ()
    buses: tuple[str, ...] = ()
    i2c_addresses: tuple[I2CAddress, ...] = ()
    features: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def render(self) -> str:
        lines = [
            f"Board: {self.board or 'unknown'}",
            f"Framework: {self.framework or 'unknown'}",
        ]
        if self.environments:
            lines.append("Environments: " + ", ".join(self.environments))
        if self.pins:
            lines.append("Pin references:")
            lines.extend(
                f"- GPIO{item.pin}: {item.symbol} ({item.source}{', ' + item.mode if item.mode else ''})"
                for item in self.pins[:60]
            )
        if self.buses:
            lines.append("Detected buses/APIs: " + ", ".join(self.buses))
        if self.i2c_addresses:
            lines.append(
                "Detected I2C addresses: "
                + ", ".join(f"{item.hex} ({item.source})" for item in self.i2c_addresses[:20])
            )
        if self.features:
            lines.append("Detected embedded features: " + ", ".join(self.features))
        if self.warnings:
            lines.append("Hardware-context warnings:")
            lines.extend(f"- {item}" for item in self.warnings)
        return "\n".join(lines)


def inspect_embedded_project(root: str | Path, *, max_files: int = 80) -> EmbeddedProjectGraph:
    workspace = Path(root).resolve(strict=True)
    board = None
    framework = None
    environments: list[str] = []
    ini = workspace / "platformio.ini"
    if ini.is_file() and not ini.is_symlink():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(ini, encoding="utf-8")
            for section in parser.sections():
                if section.startswith("env:"):
                    environments.append(section[4:])
                    board = board or parser.get(section, "board", fallback=None)
                    framework = framework or parser.get(section, "framework", fallback=None)
        except (OSError, configparser.Error):
            pass

    pins: list[HardwarePin] = []
    symbol_to_pin: dict[str, int] = {}
    symbol_modes: dict[str, str] = {}
    numeric_modes: dict[int, str] = {}
    output_refs: set[int | str] = set()
    analog_refs: set[int | str] = set()
    buses: set[str] = set()
    features: set[str] = set()
    i2c_addresses: dict[tuple[int, str], I2CAddress] = {}
    wifi_seen = False
    serial_seen = False

    source_files: list[Path] = []
    visited = 0
    ignored = {"node_modules", "vendor", "dist", "build", "__pycache__"}
    for directory, dirs, names in os.walk(workspace, followlinks=False):
        dirs[:] = sorted(
            (name for name in dirs if not name.startswith(".") and name.casefold() not in ignored
             and not (Path(directory) / name).is_symlink()
             and not getattr(os.path, "isjunction", lambda _: False)(Path(directory) / name)),
            key=lambda name: (name not in {"src", "include"}, name),
        )
        visited += 1
        if visited > 2000:
            break
        for name in sorted(names):
            path = Path(directory) / name
            if path.suffix.casefold() in {".c", ".cc", ".cpp", ".h", ".hpp", ".ino"} and path.is_file() and not path.is_symlink():
                source_files.append(path)
                if len(source_files) >= max_files:
                    break
        if len(source_files) >= max_files:
            break
    for path in source_files:
        try:
            with path.open("rb") as source:
                text = source.read(200_000).decode("utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(workspace).as_posix()
        for pattern in _PIN_PATTERNS:
            for match in pattern.finditer(text):
                symbol, pin = match.group(1), int(match.group(2))
                symbol_to_pin[symbol] = pin
                pins.append(HardwarePin(symbol, pin, rel))
        for match in _PIN_MODE_RE.finditer(text):
            ref, mode = match.group(1), match.group(2).upper()
            if ref.isdigit():
                numeric_modes[int(ref)] = mode
            else:
                symbol_modes[ref] = mode
            if mode == "OUTPUT":
                output_refs.add(int(ref) if ref.isdigit() else ref)
        for match in _OUTPUT_CALL_RE.finditer(text):
            ref = match.group(1)
            output_refs.add(int(ref) if ref.isdigit() else ref)
        for match in _ANALOG_READ_RE.finditer(text):
            ref = match.group(1)
            analog_refs.add(int(ref) if ref.isdigit() else ref)

        checks = {
            "I2C/Wire": ("Wire.begin", "TwoWire", "I2C"),
            "SPI": ("SPI.begin", "SPIClass"),
            "UART/Serial": ("Serial.begin", "HardwareSerial"),
            "PWM/LEDC": ("ledcAttach", "ledcSetup", "analogWrite"),
            "ADC": ("analogRead", "adc1_", "adc2_"),
        }
        for label, needles in checks.items():
            if any(needle in text for needle in needles):
                buses.add(label)
        serial_seen = serial_seen or "Serial.begin" in text
        wifi_seen = wifi_seen or bool(re.search(r"\b(?:WiFi|WIFI)\s*(?:\.|\.h|begin)", text))
        if wifi_seen:
            features.add("Wi-Fi")
        if re.search(r"\bBluetooth|BLEDevice|NimBLE", text):
            features.add("Bluetooth/BLE")
        if re.search(r"\battachInterrupt\s*\(", text):
            features.add("GPIO interrupts")
        if re.search(r"\b(?:esp_sleep|deepSleep|lightSleep)", text, re.I):
            features.add("low-power/sleep")

        for pattern in _I2C_ADDRESS_PATTERNS:
            for match in pattern.finditer(text):
                try:
                    address = int(match.group(1), 16)
                except ValueError:
                    continue
                if 0 <= address <= 0x7F:
                    i2c_addresses[(address, rel)] = I2CAddress(address, rel)

    # Deduplicate symbol/pin references, enriching them with inferred pinMode.
    unique: dict[tuple[str, int], HardwarePin] = {}
    for item in pins:
        mode = symbol_modes.get(item.symbol)
        unique[(item.symbol, item.pin)] = HardwarePin(item.symbol, item.pin, item.source, mode)

    def resolve_ref(ref: int | str) -> int | None:
        if isinstance(ref, int):
            return ref
        return symbol_to_pin.get(ref)

    output_pins = {pin for ref in output_refs if (pin := resolve_ref(ref)) is not None}
    analog_pins = {pin for ref in analog_refs if (pin := resolve_ref(ref)) is not None}
    output_pins.update(pin for pin, mode in numeric_modes.items() if mode == "OUTPUT")

    by_pin: dict[int, set[str]] = {}
    for item in unique.values():
        by_pin.setdefault(item.pin, set()).add(item.symbol)
    warnings = [
        f"GPIO{pin} is referenced by multiple symbols: {', '.join(sorted(symbols))}. Verify these are intentional aliases rather than competing devices."
        for pin, symbols in sorted(by_pin.items()) if len(symbols) > 1
    ]

    family = _board_family(board)
    if family == "esp32-classic":
        for pin in sorted(output_pins & _CLASSIC_ESP32_INPUT_ONLY):
            warnings.append(f"GPIO{pin} is input-only on classic ESP32 and is referenced as an output.")
        for pin in sorted(set(by_pin) & _CLASSIC_ESP32_FLASH_PINS):
            warnings.append(f"GPIO{pin} is normally connected to SPI flash on classic ESP32 modules; using it for application I/O is unsafe on common boards.")
        for pin in sorted(output_pins & _CLASSIC_ESP32_STRAPPING):
            warnings.append(f"GPIO{pin} is a classic ESP32 strapping pin; external output circuitry can affect boot if it drives the wrong level at reset.")
        if wifi_seen:
            for pin in sorted(analog_pins & _CLASSIC_ESP32_ADC2):
                warnings.append(f"GPIO{pin} uses ADC2 on classic ESP32; ADC2 reads can conflict with active Wi-Fi. Prefer an ADC1 pin when Wi-Fi is required.")
        if serial_seen:
            for pin in sorted(set(by_pin) & {1, 3}):
                warnings.append(f"GPIO{pin} is commonly used by UART0 on classic ESP32 while Serial is active; verify this assignment does not conflict with logging/programming.")

    return EmbeddedProjectGraph(
        board=board,
        framework=framework,
        environments=tuple(environments[:20]),
        pins=tuple(sorted(unique.values(), key=lambda item: (item.pin, item.symbol))[:120]),
        buses=tuple(sorted(buses)),
        i2c_addresses=tuple(sorted(i2c_addresses.values(), key=lambda item: (item.address, item.source))[:40]),
        features=tuple(sorted(features)),
        warnings=tuple(dict.fromkeys(warnings))[:40],
    )


def _board_family(board: str | None) -> str | None:
    if not board:
        return None
    value = board.casefold().replace("_", "-")
    if "esp32-s3" in value or "esp32s3" in value or "s3" in value:
        return "esp32-s3"
    if "esp32-c3" in value or "esp32c3" in value or "c3" in value:
        return "esp32-c3"
    # Common PlatformIO IDs for the original ESP32 family.  Keep the rule list
    # conservative so variant-specific constraints are not applied blindly.
    if value in {
        "esp32dev", "node32s", "nodemcu-32s", "esp-wrover-kit", "esp32doit-devkit-v1",
        "az-delivery-devkit-v4", "lolin32", "lolin-d32", "wemos-d1-mini32",
    } or value.startswith("esp32dev"):
        return "esp32-classic"
    return None
