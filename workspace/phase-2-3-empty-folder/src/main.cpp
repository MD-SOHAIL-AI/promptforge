#include <Arduino.h>

// Define the LED pin. On many ESP32 dev boards the built‑in LED is on GPIO2.
const uint8_t LED_PIN = 2;

void setup() {
    // Initialize the LED pin as an output.
    pinMode(LED_PIN, OUTPUT);
    // Optional: start serial for debugging.
    Serial.begin(115200);
    while (!Serial) { ; }
    Serial.println("ESP32 Blink Example Starting");
}

void loop() {
    digitalWrite(LED_PIN, HIGH);   // Turn the LED on
    delay(1000);                   // Wait for a second
    digitalWrite(LED_PIN, LOW);    // Turn the LED off
    delay(1000);                   // Wait for a second
}
