# ESP32 Smart Knock Pattern Detector

A small PlatformIO/Arduino project that recognizes knocks from a protected piezo input.
The firmware uses a cooperative millis() state machine. No delay() calls or Serial connection waits are used.

## Components and wiring

Use an ESP32 development board, piezoelectric knock sensor with a suitable protection/conditioning circuit,
a common-cathode RGB LED (or three LEDs), LED current-limiting resistors, an active buzzer, a push button,
and a 10k ohm resistor for an optional external button pull-up. Share the circuit ground with the ESP32.

| Component | GPIO | Connection |
| --- | --- | --- |
| Protected piezo signal | 34 / ADC1_CH6 | Conditioned analog output; GPIO34 has no internal pull-up |
| Red LED | 25 | Through a suitable LED current-limiting resistor |
| Green LED | 26 | Through a suitable LED current-limiting resistor |
| Blue LED | 27 | Through a suitable LED current-limiting resistor |
| Active buzzer | 14 | Drive via a transistor if its current exceeds GPIO capability |
| Push button | 0 | Connect to GND, INPUT_PULLUP; optional 10k pull-up to 3.3V |

GPIO0 is a boot-strapping pin. Release the button during reset/upload; remap BUTTON_PIN for a dedicated button if preferred.
An active-high common-cathode LED is assumed. Common-anode LEDs need inverted output logic.

### Piezo protection circuit

A raw piezo disc can produce large positive and negative voltage spikes. Never connect it directly to GPIO34.
Use a 3.3V-compatible conditioned sensor output, or a measured and validated analog front end with a bleed resistor
across the disc, a series current-limiting resistor, attenuation/bias as needed, and fast clamps on both polarities.
A typical starting point is a 1M ohm bleed resistor and a 100k ohm series resistor, followed by an attenuating network.
The exact network depends on the disc and surface. A diode to a 3.3V rail can conduct above 3.3V by its forward drop;
it does not guarantee the requested voltage limit. Measure the protected output under the strongest expected impact
and ensure it stays within the ESP32 input limits and below 3.3V before connecting the board. Software thresholds do
not provide electrical protection. Adjust the protection circuit or use a conditioned module if this cannot be ensured.

## Architecture

```text
src/main.cpp                  GPIO startup, button handling and state machine
src/knock/knock_detector.*     ADC threshold event and quiet-period filtering
src/pattern/pattern_manager.*  Bounded timestamps, intervals and tolerant comparison
src/output/led_controller.*    Colors and nonblocking flashes
src/output/buzzer.*            Nonblocking one/two-beep scheduling
src/storage/pattern_storage.*  Bounded Preferences/NVS save, load and validation
```

```text
IDLE -> KNOCK_DETECTED -> WAITING_FOR_NEXT_KNOCK
                         | after 2000ms without a knock
                         v
                 PATTERN_COMPLETE -> ANALYZE_PATTERN
                                      | match / no match
                                      v
                                SUCCESS / UNKNOWN -> IDLE
Long button press -> LEARNING_MODE -> saved / failed feedback -> IDLE
```

The ADC is sampled once in each loop. A reading strictly above KNOCK_THRESHOLD emits an event immediately,
including short pulses. The input must remain below threshold for DEBOUNCE_DELAY before another knock can occur.
This suppresses ringing without waiting for a pulse to last the debounce period. Tune the threshold (default 500)
and quiet period (default 30ms) for the actual protected sensor/surface.

Each accepted knock records its millis() timestamp. Subtraction forms intervals: 1500, 1900, 2700ms becomes
400, 800ms. A sequence finishes after 2000ms of silence, including a lone knock. At most 11 knocks / 10 intervals
are retained; overflow is rejected rather than matching a truncated sequence. All elapsed-time calculations
use unsigned subtraction for the 32-bit millis rollover.

| Pattern | Intervals | Output |
| --- | --- | --- |
| A: Knock - Knock | 500ms | Green, one short confirmation beep |
| B: Knock - Knock - Knock | 400ms, 400ms | Green, one short confirmation beep |
| C: Knock - pause - Knock - Knock | 800ms, 300ms | Blue, two short beeps with an audible gap |
| Learned pattern | Saved intervals | Green, one short beep |
| Unknown, single knock or overflow | No match | Red, error beep |

Timing tolerance defaults to 25% via TOLERANCE_PERCENT. A stored 500ms interval accepts 375-625ms inclusive.
Change this macro and PATTERN_TIMEOUT_MS in the header or build flags. Blue means listening, yellow means learning;
Pattern C also uses blue for its requested confirmation action. Feedback lasts 1200ms and can be interrupted by
new knocks or a button action without blocking the loop.

## Learning and persistence

Button input is debounced for 30ms. A short press clears the sequence; a hold of 1000ms enters learning once.
Releasing a handled long press does not clear the learning sequence. Learning starts yellow and ends after
2000ms of silence after the last knock, or 5000ms from entry even if there are no knocks. Enter 2-11 knocks with
intervals shorter than the sequence timeout. A successful save flashes green; insufficient data, overflow or a
failed NVS write flashes red and preserves the previously committed learned pattern.

Preferences namespace knock_storage stores the learned pattern in one blob under the key learned (7 characters).
The blob contains a format version, bounded count, intervals and a checksum. Every save checks putBytes' result;
every operation opens and closes its own handle. Loads validate length/count/checksum/intervals before replacing
in-memory data. Startup loads the committed pattern. The previous broken per-interval draft did not create valid
long NVS keys, so no valid old record is expected. No destructive NVS reset is performed.

## Build and diagnostics

```sh
pio run -e esp32dev
pio device monitor -b 115200
```

The PlatformIO project contains platformio.ini and the complete modular C++ implementation.
Example Serial Monitor output (illustrative, not a device capture):

```text
System started; no learned pattern
State: IDLE - listening
Knock #1
Knock #2
Interval: 500 ms
Pattern complete; comparing pattern...
Pattern A recognized
```

Raw ADC readings are not continuously printed. Logs describe state changes, knock counts, intervals,
recognized patterns and storage failures.

## Testing procedure and troubleshooting

1. Verify the protection circuit and voltage limits before connecting GPIO34.
2. Build with PlatformIO, upload explicitly, and open the Serial Monitor at 115200.
3. Try Pattern A, B and C; tune threshold/quiet time/tolerance for the actual sensor.
4. Check a single knock and an unrelated rhythm produce red/error feedback and return to listening.
5. Confirm a short button press clears a partial sequence. Hold for one second to learn; record a custom rhythm.
6. Confirm green save feedback, restart the board, and repeat the learned rhythm.
7. Enter learning without knocking: it must time out at five seconds. Test one knock and an overlong sequence.
8. Confirm button/input responsiveness while confirmation beeps and LED flashes are active.

For no detection, inspect protection attenuation, ADC pin mapping, ground and threshold. For double counts,
increase quiet time and inspect mechanical ringing. For inverted LED colors, check common-anode wiring.
If NVS is unavailable or saving fails, inspect serial diagnostics and storage availability; failed learning
must not overwrite the committed pattern. Release GPIO0 during upload/reset if the board enters its bootloader.

Host regression checks in tests/host execute the actual detector, pattern manager, storage, outputs and loop
with hardware API stubs. They exercise short pulses/ringing, tolerance boundaries, timer rollover, bounded
sequences, button actions, learning deadlines, failed NVS writes and save/load across object/application restart.
Run them with the supplied run_host_tests.py script and a C++ compiler (portable Zig is supported).

Physical ESP32 knock recognition, sensor calibration, analog voltage protection and actual flash persistence
have not been tested on hardware. Host stubs test firmware control logic; they cannot verify those device properties.
