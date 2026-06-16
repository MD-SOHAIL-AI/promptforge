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

void connectToWiFi() {
    Serial.print("Connecting to ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    uint8_t attempt = 0;
    while (WiFi.status() != WL_CONNECTED && attempt < 30) {
        delay(500);
        Serial.print('.');
        attempt++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        Serial.println();
        Serial.print("Connected! IP address: ");
        Serial.println(WiFi.localIP());
    } else {
        Serial.println();
        Serial.println("Failed to connect to WiFi");
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000); // Give time for Serial monitor to start
    dht.begin();
    connectToWiFi();
}

void loop() {
    // Wait a few seconds between measurements.
    delay(2000);

    // Reading temperature or humidity takes about 250ms!
    // Sensor readings may also be up to 2 seconds 'old' (its a very slow sensor)
    float humidity = dht.readHumidity();
    float temperature = dht.readTemperature(); // Celsius by default

    // Check if any reads failed and exit early (to try again).
    if (isnan(humidity) || isnan(temperature)) {
        Serial.println(F("Failed to read from DHT sensor!"));
        return;
    }

    Serial.print(F("Humidity: "));
    Serial.print(humidity);
    Serial.print(F(" %\t"));
    Serial.print(F("Temperature: "));
    Serial.print(temperature);
    Serial.println(F(" *C"));

    // Optionally, you could send this data to a server here.
}
