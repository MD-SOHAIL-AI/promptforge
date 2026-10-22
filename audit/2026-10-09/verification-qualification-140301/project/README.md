# Components
ESP32, DHT11, LED, 220 ohm resistor, breadboard and wires.
# Pin connections
| Component | ESP32 pin |
| --- | --- |
| DHT11 data | GPIO4 |
| LED anode via resistor | GPIO2 |
Power the sensor from 3.3V and connect all grounds together; LED cathode goes to ground.
# Libraries and upload
In Arduino IDE Boards Manager install esp32 by Espressif Systems. In Library Manager
install DHT sensor library by Adafruit and Adafruit Unified Sensor. Select ESP32 Dev Module
and the correct serial port. Create a new Arduino sketch in a same-named folder. Copy src/main.cpp into the .ino tab;
all dependencies are installed libraries, so no extra local headers are needed. Click Verify,
then Upload via USB. If the IDE cannot connect, hold BOOT while it shows Connecting.
Open Serial Monitor at 115200 baud to see the readings.
# Explanation
setup initializes serial output, LED and sensor; loop reads every two seconds and
switches the LED on above 30 degrees, off otherwise. Invalid readings are skipped.
# Expected output
25.0 45.0 (temperature in Celsius and relative humidity in percent).
# Troubleshooting
Check GPIO4 and ground wiring for failed readings; check USB cable, board and port
if upload fails. Install both libraries if compilation reports missing DHT.h.
