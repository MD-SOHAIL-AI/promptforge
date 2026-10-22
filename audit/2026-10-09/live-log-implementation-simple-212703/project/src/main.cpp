/*
  ESP32 Automatic Temperature Monitor
  Reads temperature and humidity from DHT11 sensor
  Prints readings to Serial Monitor
  Turns on LED when temperature > 30°C
*/

#include <Arduino.h>
#include <DHT.h>

// Pin definitions
#define DHT_PIN 4      // GPIO4 connected to DHT11 data pin
#define LED_PIN 2      // GPIO2 connected to LED (with resistor)

// DHT sensor type
#define DHT_TYPE DHT11

// Create DHT object
DHT dht(DHT_PIN, DHT_TYPE);

// Variables for temperature and humidity
float temperature;
float humidity;

// Timing variables
unsigned long lastReadTime = 0;
const unsigned long readInterval = 2000; // Read every 2 seconds

void setup() {
  // Initialize serial communication
  Serial.begin(115200);
  
  // Wait for serial to initialize
  while (!Serial) {
    delay(10);
  }
  
  // Initialize LED pin as output
  pinMode(LED_PIN, OUTPUT);
  
  // Initialize DHT sensor
  dht.begin();
  
  // Print startup message
  Serial.println("ESP32 Automatic Temperature Monitor");
  Serial.println("====================================");
  Serial.println("Reading temperature and humidity from DHT11...");
  Serial.println();
}

void loop() {
  // Check if it's time to read the sensor
  unsigned long currentTime = millis();
  
  if (currentTime - lastReadTime >= readInterval) {
    // Save the current time for next reading
    lastReadTime = currentTime;
    
    // Read temperature and humidity from DHT11
    humidity = dht.readHumidity();
    temperature = dht.readTemperature();
    
    // Check if reading failed
    if (isnan(humidity) || isnan(temperature)) {
      Serial.println("Failed to read from DHT11 sensor!");
      return;
    }
    
    // Print readings to Serial Monitor
    Serial.print("Temperature: ");
    Serial.print(temperature);
    Serial.print("°C | Humidity: ");
    Serial.print(humidity);
    Serial.println("%");
    
    // Control LED based on temperature
    if (temperature > 30.0) {
      digitalWrite(LED_PIN, HIGH);  // Turn LED ON
      Serial.println("Temperature > 30°C - LED ON");
    } else {
      digitalWrite(LED_PIN, LOW);   // Turn LED OFF
      Serial.println("Temperature <= 30°C - LED OFF");
    }
    
    Serial.println();  // Empty line for readability
  }
}