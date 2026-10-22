#include <Arduino.h>

void setup() {
  Serial.begin(115200);
  pinMode(2, OUTPUT);
}

void loop() {
  digitalWrite(2, HIGH);
  Serial.println("HEARTBEAT");
  delay(1000);
  digitalWrite(2, LOW);
  Serial.println("HEARTBEAT");
  delay(1000);
}