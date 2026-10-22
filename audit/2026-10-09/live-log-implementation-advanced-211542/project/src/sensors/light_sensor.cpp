/**
 * @file light_sensor.cpp
 * @brief Light sensor implementation using LDR (Light Dependent Resistor)
 */

#include "light_sensor.h"

LightSensor::LightSensor(uint8_t pin) 
    : pin(pin), sensorInitialized(false), lastReadTime(0), lastRawValue(0) {
    pinMode(pin, INPUT);
}

LightSensor::~LightSensor() {}

bool LightSensor::begin() {
    sensorInitialized = true;
    lastReadTime = millis();
    return true;
}

bool LightSensor::readSensor(float& lightLevel) {
    if (!sensorInitialized) {
        return false;
    }
    
    unsigned long currentTime = millis();
    if (currentTime - lastReadTime < READ_INTERVAL) {
        return false;
    }
    
    lastRawValue = analogRead(pin);
    // Convert to percentage (0-100%) where 0 is dark and 100 is bright
    lightLevel = map(lastRawValue, 0, 4095, 0, 100);
    
    lastReadTime = currentTime;
    return true;
}

bool LightSensor::isAvailable() const {
    return sensorInitialized;
}

String LightSensor::getStatusString() const {
    if (!sensorInitialized) {
        return "LIGHT: NOT INIT";
    }
    return "LIGHT: OK";
}

uint16_t LightSensor::getRawValue() const {
    return lastRawValue;
}