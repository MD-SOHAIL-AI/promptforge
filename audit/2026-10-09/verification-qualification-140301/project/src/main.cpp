#include <Arduino.h>
#include <DHT.h>
constexpr int LED_PIN = 2;
DHT sensor(4, DHT11);
void setup() { Serial.begin(115200); pinMode(LED_PIN, OUTPUT); sensor.begin(); }
void loop() {
  const float temperature = sensor.readTemperature();
  const float humidity = sensor.readHumidity();
  if (!isnan(temperature) && !isnan(humidity)) {
    Serial.print(temperature); Serial.print(" "); Serial.println(humidity);
    digitalWrite(LED_PIN, temperature > 30.0f ? HIGH : LOW);
  }
  delay(2000);
}
