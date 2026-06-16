#include <Arduino.h>
#include <WiFi.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>

// Replace with your network credentials
const char* ssid     = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// DHT22 (AM2302) pin configuration
#define DHTPIN 4          // GPIO where the DHT22 is connected
#define DHTTYPE DHT22    // DHT 22 (AM2302)

DHT_Unified dht(DHTPIN, DHTTYPE);
uint32_t delayMS;

void connectToWiFi() {
    Serial.print("Connecting to ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    uint8_t retry = 0;
    while (WiFi.status() != WL_CONNECTED && retry < 30) {
        delay(500);
        Serial.print('.');
        ++retry;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nFailed to connect to WiFi");
    }
}

void setup() {
    Serial.begin(115200);
    while (!Serial) { delay(10); }
    connectToWiFi();

    // Initialize DHT sensor.
    dht.begin();
    sensor_t sensor;
    dht.temperature().getSensor(&sensor);
    Serial.println(F("--- Temperature Sensor ---"));
    Serial.print(F("Sensor Type: ")); Serial.println(sensor.name);
    Serial.print(F("Driver Ver: ")); Serial.println(sensor.version);
    Serial.print(F("Unique ID: ")); Serial.println(sensor.sensor_id);
    Serial.print(F("Max Value: ")); Serial.print(sensor.max_value); Serial.println(F(" *C"));
    Serial.print(F("Min Value: ")); Serial.print(sensor.min_value); Serial.println(F(" *C"));
    Serial.print(F("Resolution: ")); Serial.print(sensor.resolution); Serial.println(F(" *C"));
    Serial.println();

    dht.humidity().getSensor(&sensor);
    Serial.println(F("--- Humidity Sensor ---"));
    Serial.print(F("Sensor Type: ")); Serial.println(sensor.name);
    Serial.print(F("Driver Ver: ")); Serial.println(sensor.version);
    Serial.print(F("Unique ID: ")); Serial.println(sensor.sensor_id);
    Serial.print(F("Max Value: ")); Serial.print(sensor.max_value); Serial.println(F(" %"));
    Serial.print(F("Min Value: ")); Serial.print(sensor.min_value); Serial.println(F(" %"));
    Serial.print(F("Resolution: ")); Serial.print(sensor.resolution); Serial.println(F(" %"));
    Serial.println();

    // Set delay between sensor readings based on sensor's minimum delay.
    dht.temperature().getSensor(&sensor);
    delayMS = sensor.min_delay / 1000;
    if (delayMS < 2000) delayMS = 2000; // enforce a reasonable minimum
}

void loop() {
    // Wait between measurements.
    delay(delayMS);

    // Get temperature event and print.
    sensors_event_t event;
    dht.temperature().getEvent(&event);
    if (isnan(event.temperature)) {
        Serial.println(F("Error reading temperature!"));
    } else {
        Serial.print(F("Temperature: ")); Serial.print(event.temperature); Serial.println(F(" *C"));
    }

    // Get humidity event and print.
    dht.humidity().getEvent(&event);
    if (isnan(event.relative_humidity)) {
        Serial.println(F("Error reading humidity!"));
    } else {
        Serial.print(F("Humidity: ")); Serial.print(event.relative_humidity); Serial.println(F(" %"));
    }
}
