/**
 * @file air_quality_sensor.cpp
 * @brief Air quality sensor implementation using MQ-135
 */

#include "air_quality_sensor.h"
#include <math.h>

AirQualitySensor::AirQualitySensor(uint8_t pin) 
    : pin(pin), sensorInitialized(false), lastReadTime(0), lastPPM(0.0) {
    pinMode(pin, INPUT);
}

AirQualitySensor::~AirQualitySensor() {}

bool AirQualitySensor::begin() {
    sensorInitialized = true;
    lastReadTime = millis();
    return true;
}

bool AirQualitySensor::readSensor(float& airQuality) {
    if (!sensorInitialized) {
        return false;
    }
    
    unsigned long currentTime = millis();
    if (currentTime - lastReadTime < READ_INTERVAL) {
        return false;
    }
    
    int rawValue = analogRead(pin);
    // Convert to voltage (assuming 3.3V reference and 12-bit ADC)
    float voltage = (rawValue / 4095.0) * 3.3;
    
    // Simple conversion to PPM (this would need calibration for accurate readings)
    // MQ-135 typical response curve approximation
    if (voltage > 0.1) {
        lastPPM = pow(10, ((log10(voltage) - (-0.4)) / -0.3)); // Simplified formula
    } else {
        lastPPM = 0.0;
    }
    
    airQuality = lastPPM;
    lastReadTime = currentTime;
    return true;
}

bool AirQualitySensor::isAvailable() const {
    return sensorInitialized;
}

String AirQualitySensor::getStatusString() const {
    if (!sensorInitialized) {
        return "AIRQ: NOT INIT";
    }
    return "AIRQ: OK";
}

float AirQualitySensor::getPPM() const {
    return lastPPM;
}