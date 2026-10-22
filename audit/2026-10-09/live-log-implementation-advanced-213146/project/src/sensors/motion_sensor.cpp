/**
 * @file motion_sensor.cpp
 * @brief Motion sensor implementation using PIR sensor
 */

#include "motion_sensor.h"

MotionSensor::MotionSensor(uint8_t pin) 
    : pin(pin), sensorInitialized(false), lastReadTime(0), lastMotionState(false) {
    pinMode(pin, INPUT);
}

MotionSensor::~MotionSensor() {}

bool MotionSensor::begin() {
    sensorInitialized = true;
    lastReadTime = millis();
    return true;
}

bool MotionSensor::readSensor(bool& motionDetected) {
    if (!sensorInitialized) {
        return false;
    }
    
    unsigned long currentTime = millis();
    if (currentTime - lastReadTime < READ_INTERVAL) {
        return false;
    }
    
    lastMotionState = digitalRead(pin) == HIGH;
    motionDetected = lastMotionState;
    
    lastReadTime = currentTime;
    return true;
}

bool MotionSensor::isAvailable() const {
    return sensorInitialized;
}

String MotionSensor::getStatusString() const {
    if (!sensorInitialized) {
        return "MOTION: NOT INIT";
    }
    return "MOTION: OK";
}

bool MotionSensor::getLastMotionState() const {
    return lastMotionState;
}