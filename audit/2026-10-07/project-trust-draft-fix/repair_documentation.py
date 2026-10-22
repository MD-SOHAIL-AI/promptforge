"""Replace contradictory generated wiring claims with source-derived mappings."""
import json
from pathlib import Path

audit = Path(__file__).parent
context = json.loads((audit / "repair-context.json").read_text(encoding="utf-8"))
stage = Path(context["repair_stage"])
(stage / "docs/hardware_mapping.md").write_text('''# Hardware mapping used by this firmware

These assignments come from the generated source and are preserved by the repair.

| Component | ESP32 GPIO | Source |
| --- | --- | --- |
| DHT22 data | 4 | `src/sensors/temperature_sensor.cpp` |
| MQ-135 analog input | 34 | `src/sensors/air_quality_sensor.h` |
| LDR analog input | 35 | `src/sensors/light_sensor.h` |
| PIR input | 32 | `src/sensors/motion_sensor.h` |
| Fan relay | 26 | `src/control/relay_controller.h` |
| Light relay | 27 | `src/control/relay_controller.h` |
| SSD1306 SDA | 21 | Default ESP32 `Wire.begin()` |
| SSD1306 SCL | 22 | Default ESP32 `Wire.begin()` |
| SD chip select | 5 | `src/storage/data_logger.h` |
| SD clock | 18 | Default ESP32 SPI bus |
| SD MISO | 19 | Default ESP32 SPI bus |
| SD MOSI | 23 | Default ESP32 SPI bus |

The display uses I2C address `0x3C` and a 128 by 64 screen. Relay controls are
active LOW. Buzzer state is currently logged in software; no buzzer output
pin is driven. Physical buttons and status LEDs are not implemented. The
watchdog's GPIO4 declaration is unused; external watchdog output is disabled.

The original draft's GPIO13/14 analog, GPIO15 PIR and reversed OLED SDA/SCL
documentation did not match its source. Follow the table above. Do not share
SD GPIO18/19/23 with the draft's proposed LEDs, buzzer or button wiring.

Verify each module's supply voltage and output level against its datasheet
before connecting it. ESP32 inputs must stay within their electrical limits;
an MQ module powered at 5 V can require conditioning of its analog output.
Sensor calibration and the assembled circuit have not been verified by a build.
''', encoding="utf-8")
print("Wiring documentation now matches the preserved source pins.")
