#include <Arduino.h>
#include <WiFi.h>
#include <DHT.h>

// WiFi credentials – replace with your network SSID and password
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// DHT22 configuration
#define DHTPIN 4          // GPIO where the DHT22 data pin is connected
#define DHTTYPE DHT22    // DHT 22 (AM2302)
DHT dht(DHTPIN, DHTTYPE);

void connectWiFi() {
    Serial.print("Connecting to ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    uint8_t attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 30) {
        delay(500);
        Serial.print('.');
        attempts++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("\nWiFi connected!");
        Serial.print("IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println("\nFailed to connect to WiFi.");
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000); // give time for serial monitor to attach
    Serial.println("ESP32 Weather Station starting...");

    dht.begin();
    connectWiFi();
}

void loop() {
    // Wait a few seconds between measurements.
    delay(2000);

    // Reading temperature or humidity takes about 250ms!
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius by default

    // Check if any reads failed and exit early (to try again).
    if (isnan(humidity) || isnan(temperature)) {
        Serial.println("Failed to read from DHT sensor!");
        return;
    }

    Serial.printf("Temperature: %.1f °C  Humidity: %.1f %%\n", temperature, humidity);

    // If WiFi is connected, you could send data to a server here.
    if (WiFi.status() == WL_CONNECTED) {
        // Placeholder for future HTTP/MQTT transmission.
    }
}
