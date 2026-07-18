# ForgeX Multi-Board Platform Architecture

Generated: 2026-06-17

## Goal

Evolve static V1 board JSON into a scalable board registry, plugin system, capability detector, template system, and SDK/toolchain registry.

## Current V1 Board Support

Current source:

- `backend/hardware/boards/esp32.json`
- `backend/hardware/boards/arduino_uno.json`
- `backend/hardware/boards/stm32.json`
- `backend/tools/board_detector.py`
- `backend/hardware/compatibility/compatibility_matrix.py`
- `backend/hardware/constraints/constraint_engine.py`
- `backend/validation/board_validator.py`

Current families:

- ESP32
- Arduino Uno
- STM32

## Target Families

- ESP32
- ESP32-C3
- ESP32-S3
- ESP32-C6
- ESP8266
- RP2040
- Raspberry Pi Pico
- STM32 families
- NRF52
- Teensy
- AVR boards
- Custom boards

## Board Registry

```text
BoardRegistry
  list_boards(filters)
  get_board(board_id)
  resolve_alias(name)
  match_device(usb_identity, serial_hint)
  capabilities(board_id)
  frameworks(board_id)
  templates(board_id, framework)
```

Board record:

```text
board_id
family
variant
display_name
vendor
mcu
architecture
flash_bytes
ram_bytes
clock_hz
frameworks
platformio_board_ids
upload_protocols
debug_protocols
usb_vid_pid
pin_map
peripherals
capabilities
simulation_support
toolchain_refs
template_refs
metadata_version
plugin_id
```

## Board Plugin System

Plugin package:

```text
forgex-board-plugin/
  plugin.json
  boards/*.json
  templates/{framework}/{board_id}/...
  validators/*.py
  toolchains/*.json
  docs/*.md
```

Manifest:

```json
{
  "plugin_id": "forgex.esp32",
  "name": "ESP32 Boards",
  "version": "1.0.0",
  "boards": ["boards/esp32-devkit-v1.json"],
  "templates": ["templates/platformio/esp32-devkit-v1"],
  "toolchains": ["toolchains/platformio-espressif32.json"]
}
```

Plugin rules:

- Plugins cannot execute arbitrary code during discovery.
- Python validators run only in a controlled backend plugin sandbox.
- Board JSON schema is versioned.
- Plugin install/update requires user approval.
- Custom local board plugins are allowed.

## Capability Detection

Detection sources:

- USB VID/PID and serial number.
- Serial product/manufacturer strings.
- PlatformIO board metadata.
- Optional probe commands.
- OpenOCD/ST-Link/J-Link discovery.
- Firmware handshake, when user permits.

Detected device:

```text
device_id
port
serial_number
vid
pid
manufacturer
product
matched_board_id
match_confidence
capabilities
last_seen
trusted
```

## Board Templates

Template types:

- Blink.
- Serial hello.
- Sensor read.
- WiFi client.
- BLE peripheral.
- I2C scanner.
- SPI device.
- FreeRTOS task.
- Low-power.
- Bootloader/OTA.

Template variables:

- board ID.
- framework.
- pins.
- baud.
- libraries.
- upload/debug protocol.

Templates must generate valid PlatformIO projects first, then later support vendor-native SDK projects.


## Board SDK Registry

SDK/toolchain record:

```text
toolchain_id
name
type
version
install_path
install_state
manager
supported_families
commands
health_check
```

Initial SDKs:

- PlatformIO Core.
- Espressif platform packages.
- Arduino AVR.
- STM32 platform packages.
- OpenOCD.
- esptool.
- dfu-util.
- st-flash.
- J-Link tools.

## Validation Pipeline

For every board/project:

1. Resolve board and framework.
2. Validate board/framework compatibility.
3. Validate pin usage.
4. Validate peripheral counts.
5. Validate memory estimates.
6. Validate library compatibility.
7. Validate upload/debug protocol.
8. Validate selected physical device.

## V1 Migration

1. Wrap existing JSON files in `BoardRegistry`.
2. Add schema version to board JSON.
3. Move hard-coded board aliases from detector/planner into registry aliases.
4. Add device registry around `BoardDetector`.
5. Add board manager API/UI.
6. Add plugin loading after schema stabilizes.

