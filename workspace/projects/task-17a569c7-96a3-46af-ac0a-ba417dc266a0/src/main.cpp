#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <DHT_U.h>

// WiFi credentials (replace with your network details)
const char* ssid = "YOUR_SSID";
const char* password = "YOUR_PASSWORD";

// DHT22 configuration
#define DHTPIN 4          // GPIO where the DHT22 is connected
#define DHTTYPE DHT22    // DHT 22 (AM2302)
DHT_Unified dht(DHTPIN, DHTTYPE);

WebServer server(80);

float temperature = NAN;
float humidity = NAN;

void handleRoot() {
    String html = "<html><head><title>ESP32 Weather Station</title></head><body>";
    html += "<h1>Current Weather</h1>";
    if (isnan(temperature) || isnan(humidity)) {
        html += "<p>Sensor data not available.</p>";
    } else {
        html += "<p>Temperature: " + String(temperature, 1) + " &deg;C</p>";
        html += "<p>Humidity: " + String(humidity, 1) + " %</p>";
    }
    html += "</body></html>";
    server.send(200, "text/html", html);
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println();
    Serial.println("ESP32 Weather Station starting...");

    // Initialize DHT sensor
    dht.begin();
    sensor_t sensor;
    dht.temperature().getSensor(&sensor);
    Serial.println("--- Temperature Sensor ---");
    Serial.print("Sensor: "); Serial.println(sensor.name);
    Serial.print("Version: "); Serial.println(sensor.version);
    Serial.print("ID: "); Serial.println(sensor.sensor_id);
    Serial.print("Min Value: "); Serial.println(sensor.min_value);
    Serial.print("Max Value: "); Serial.println(sensor.max_value);
    Serial.print("Resolution: "); Serial.println(sensor.resolution);

    dht.humidity().getSensor(&sensor);
    Serial.println("--- Humidity Sensor ---");
    Serial.print("Sensor: "); Serial.println(sensor.name);
    Serial.print("Version: "); Serial.println(sensor.version);
    Serial.print("ID: "); Serial.println(sensor.sensor_id);
    Serial.print("Min Value: "); Serial.println(sensor.min_value);
    Serial.print("Max Value: "); Serial.println(sensor.max_value);
    Serial.print("Resolution: "); Serial.println(sensor.resolution);

    // Connect to WiFi
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    Serial.print("Connecting to WiFi ..");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print('.');
    }
    Serial.println();
    Serial.print("Connected! IP address: ");
    Serial.println(WiFi.localIP());

    // Start web server
    server.on("/", handleRoot);
    server.begin();
    Serial.println("HTTP server started");
}

void loop() {
    // Handle client requests
    server.handleClient();

    // Read sensor every 2 seconds
    static unsigned long lastRead = 0;
    if (millis() - lastRead >= 2000) {
        lastRead = millis();
        sensors_event_t event;
        dht.temperature().getEvent(&event);
        if (!isnan(event.temperature)) {
            temperature = event.temperature;
        }
        dht.humidity().getEvent(&event);
        if (!isnan(event.relative_humidity)) {
            humidity = event.relative_humidity;
        }
        Serial.printf("Temp: %.1f C  Humidity: %.1f %%\n", temperature, humidity);
    }
}
