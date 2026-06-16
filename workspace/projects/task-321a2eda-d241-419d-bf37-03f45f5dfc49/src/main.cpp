#include <Arduino.h>
#include <WiFi.h>
#include <DHT.h>

// WiFi credentials
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// DHT22 configuration
#define DHTPIN 4          // GPIO where the DHT22 is connected
#define DHTTYPE DHT22    // DHT 22 (AM2302)
DHT dht(DHTPIN, DHTTYPE);

void connectWiFi() {
    Serial.print("Connecting to ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    int retry = 0;
    while (WiFi.status() != WL_CONNECTED && retry < 20) {
        delay(500);
        Serial.print('.');
        retry++;
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
    delay(1000);
    Serial.println("ESP32 Weather Station Starting...");
    dht.begin();
    connectWiFi();
}

void loop() {
    // Wait a few seconds between measurements.
    delay(2000);

    // Reading temperature or humidity takes about 250ms!
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius

    // Check if any reads failed and exit early (to try again).
    if (isnan(humidity) || isnan(temperature)) {
        Serial.println("Failed to read from DHT sensor!");
        return;
    }

    Serial.print("Temperature: ");
    Serial.print(temperature);
    Serial.print(" *C  ");
    Serial.print("Humidity: ");
    Serial.print(humidity);
    Serial.println(" %");

    // Optionally, you could send this data to a server here.
}
