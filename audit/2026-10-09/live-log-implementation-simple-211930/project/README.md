# ESP32 Automatic Temperature Monitor

A simple beginner-friendly project that monitors temperature and humidity using an ESP32 and DHT11 sensor, with LED warning indicator.

## Required Components

- ESP32 development board (ESP32 DevKit recommended)
- DHT11 temperature and humidity sensor
- LED (any color)
- 220Ω resistor (for LED)
- Breadboard and jumper wires
- USB cable for programming

## Pin Connections

| Component | ESP32 Pin | Connection Details |
|-----------|-----------|-------------------|
| DHT11 Data Pin | GPIO 4 | Connect to DHT11 data pin |
| DHT11 VCC | 3.3V | Connect to 3.3V power |
| DHT11 GND | GND | Connect to ground |
| LED Anode (+) | GPIO 2 | Through 220Ω resistor |
| LED Cathode (-) | GND | Connect to ground |

## Circuit/Wiring Explanation

1. **DHT11 Sensor**: Connect the VCC pin to 3.3V, GND to ground, and data pin to GPIO 4 on the ESP32
2. **LED**: Connect the anode (longer leg) through a 220Ω resistor to GPIO 2, and cathode (shorter leg) to ground
3. The ESP32 will read temperature and humidity every 2 seconds and display readings in the Serial Monitor
4. When temperature exceeds 30°C, the LED turns ON as a warning indicator

## Required Libraries

### DHT Sensor Library
1. Open Arduino IDE
2. Go to Sketch → Include Library → Manage Libraries...
3. Search for "DHT sensor library" by Adafruit
4. Install the latest version
5. Also install the "Adafruit Unified Sensor" library when prompted

## Step-by-Step Instructions to Upload Code

1. **Install ESP32 Board Support** (if not already installed):
   - Open Arduino IDE
   - Go to File → Preferences
   - In "Additional Boards Manager URLs", add: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
   - Go to Tools → Board → Boards Manager
   - Search for "ESP32" and install "ESP32 by Espressif Systems"

2. **Select Your Board**:
   - Tools → Board → ESP32 Arduino → ESP32 Dev Module

3. **Connect Your ESP32**:
   - Connect ESP32 to computer via USB cable
   - Select the correct port: Tools → Port → [your ESP32 port]

4. **Upload the Code**:
   - Copy the code from `src/main.cpp` into a new Arduino sketch
   - Click the Upload button (right arrow icon)
   - Wait for upload to complete

5. **Open Serial Monitor**:
   - Click the Serial Monitor button (magnifying glass)
   - Set baud rate to 115200
   - You should see temperature and humidity readings every 2 seconds

## Code Explanation for Beginners

### Key Sections:
- **Library Includes**: `#include <DHT.h>` adds the DHT sensor library
- **Pin Definitions**: `#define DHT_PIN 4` and `#define LED_PIN 2` specify which GPIO pins to use
- **Setup Function**: Runs once at startup to initialize serial, LED pin, and DHT sensor
- **Loop Function**: Runs repeatedly to:
  1. Check if 2 seconds have passed
  2. Read temperature and humidity from DHT11
  3. Print values to Serial Monitor
  4. Turn LED ON if temperature > 30°C, otherwise OFF

### How It Works:
- Uses `millis()` for non-blocking timing (better than `delay()`)
- Reads DHT11 every 2 seconds to avoid sensor overload
- Handles sensor read errors gracefully
- Provides clear Serial Monitor output with labels

## Expected Serial Monitor Output

```
ESP32 Automatic Temperature Monitor
====================================
Reading temperature and humidity from DHT11...

Temperature: 25.30°C | Humidity: 45.00%
Temperature <= 30°C - LED OFF

Temperature: 25.50°C | Humidity: 44.80%
Temperature <= 30°C - LED OFF

Temperature: 31.20°C | Humidity: 42.10%
Temperature > 30°C - LED ON

Temperature: 30.80°C | Humidity: 41.90%
Temperature > 30°C - LED ON
```

## Common Errors and Troubleshooting Steps

### 1. "Failed to read from DHT11 sensor!"
**Causes**: Wiring issues, wrong pin, sensor not powered
**Solutions**:
- Check wiring connections (VCC to 3.3V, GND to GND, data to GPIO 4)
- Ensure DHT11 is properly seated in breadboard
- Try different GPIO pin if GPIO 4 doesn't work
- Add a 10kΩ pull-up resistor between VCC and data line

### 2. Upload fails or board not detected
**Causes**: Wrong board selection, cable issues, driver problems
**Solutions**:
- Verify correct board selected: ESP32 Dev Module
- Check USB cable (use data cable, not charging-only)
- Install CP210x or CH340 drivers if needed
- Try holding BOOT button during upload

### 3. Garbled text in Serial Monitor
**Cause**: Baud rate mismatch
**Solution**: Set Serial Monitor to 115200 baud (matches `Serial.begin(115200)`)

### 4. LED not working
**Causes**: Wrong polarity, missing resistor, wrong pin
**Solutions**:
- Check LED orientation (long leg to resistor, short leg to GND)
- Verify 220Ω resistor is in series with LED
- Confirm LED is connected to GPIO 2
- Test LED with Blink example to verify pin works

### 5. Temperature readings seem wrong
**Causes**: Sensor needs time to stabilize, poor connections
**Solutions**:
- Wait 1-2 minutes after powering on for sensor to stabilize
- Check all connections are secure
- Ensure sensor is not touching heat sources
- Try a different DHT11 sensor if available

## Tips for Beginners

1. **Double-check wiring** before powering on
2. **Start with basic blink** to verify ESP32 works
3. **Use colored wires** for easier troubleshooting (red=power, black=ground, yellow=data)
4. **Don't disconnect while uploading** - wait for "Done uploading" message
5. **Sensor readings update every 2 seconds** - be patient for changes
6. **LED provides visual feedback** - you can see warnings without Serial Monitor

## Safety Notes

- ESP32 operates at 3.3V logic - do not connect 5V signals directly to GPIO pins
- DHT11 works well with 3.3V power from ESP32
- Always use current-limiting resistor with LED (220Ω recommended)
- Disconnect USB when making wiring changes