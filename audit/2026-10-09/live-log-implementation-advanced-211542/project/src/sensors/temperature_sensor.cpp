/**
 * @file temperature_sensor.cpp
 * @brief Temperature and humidity sensor implementation using DHT22
 */

#include "temperature_sensor.h"

TemperatureSensor::TemperatureSensor(uint8_t pin) 
    : pin(pin), dht(nullptr), sensorInitialized(false), lastReadTime(0) {}

TemperatureSensor::~TemperatureSensor() {
    if (dht) {
        delete dht;
        dht = nullptr;
    }
}

bool TemperatureSensor::begin() {
    try {
        dht = new DHT(pin, DHT22);
        dht->begin();
        sensorInitialized = true;
        lastReadTime = millis();
        return true;
    } catch (...) {
        sensorInitialized = false;
        return false;
    }
}

bool TemperatureSensor::readSensor(float& temperature, float& humidity) {
    if (!sensorInitialized || !dht) {
        return false;
    }
    
    unsigned long currentTime = millis();
    if (currentTime - lastReadTime < READ_INTERVAL) {
        return false; // Not time to read yet
    }
    
    temperature = dht->readTemperature();
    humidity = dht->readHumidity();
    
    // Check if readings are valid
    if (isnan(temperature) || isnan(humidity)) {
        return false;
    }
    
    lastReadTime = currentTime;
    return true;
}

bool TemperatureSensor::isAvailable() const {
    return sensorInitialized;
}

String TemperatureSensor::getStatusString() const {
    if (!sensorInitialized) {
        return "TEMP: NOT INIT";
    }
    if (!dht) {
        return "TEMP: NO SENSOR";
    }
    return "TEMP: OK";
}