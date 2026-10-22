/**
 * @file automation_controller.cpp
 * @brief Automation controller implementation for environmental control logic
 */

#include "automation_controller.h"

AutomationController::AutomationController(
    TemperatureSensor& tempSensor,
    AirQualitySensor& airQualitySensor,
    LightSensor& lightSensor,
    MotionSensor& motionSensor,
    RelayController& relayController
) 
    : temperatureSensor(tempSensor),
      airQualitySensor(airQualitySensor),
      lightSensor(lightSensor),
      motionSensor(motionSensor),
      relayController(relayController),
      temperatureThreshold(25.0),
      airQualityThreshold(1000.0),
      lightThreshold(30),
      motionTimeout(30000),
      hysteresis(2.0),
      autoModeEnabled(true),
      lastMotionTime(0),
      fanState(false),
      lightState(false) {}

AutomationController::~AutomationController() {}

void AutomationController::begin() {
    // Set initial states
    relayController.setFan(false);
    relayController.setLight(false);
    relayController.setBuzzer(false);
}

void AutomationController::update() {
    if (!autoModeEnabled) {
        return;
    }
    
    // Read sensor values
    float temperature = 0.0, humidity = 0.0;
    float airQuality = 0.0;
    uint8_t lightLevel = 0;
    bool motionDetected = false;
    
    bool tempOk = temperatureSensor.readSensor(temperature, humidity);
    bool airOk = airQualitySensor.readSensor(airQuality);
    bool lightOk = lightSensor.readSensor(lightLevel);
    bool motionOk = motionSensor.readSensor(motionDetected);
    
    // Update motion timestamp if motion detected
    if (motionDetected) {
        lastMotionTime = millis();
    }
    
    // Control outputs based on sensor readings
    if (tempOk) {
        controlFan(temperature);
    }
    
    if (lightOk && motionOk) {
        controlLight(lightLevel, motionDetected);
    }
    
    if (airOk) {
        controlBuzzer(airQuality);
    }
}

void AutomationController::setTemperatureThreshold(float threshold) {
    temperatureThreshold = threshold;
}

void AutomationController::setAirQualityThreshold(float threshold) {
    airQualityThreshold = threshold;
}

void AutomationController::setLightThreshold(uint8_t threshold) {
    lightThreshold = threshold;
}

void AutomationController::setMotionTimeout(unsigned long timeout) {
    motionTimeout = timeout;
}

void AutomationController::setHysteresis(float hysteresisValue) {
    hysteresis = hysteresisValue;
}

void AutomationController::setAutoMode(bool enabled) {
    autoModeEnabled = enabled;
    // If switching to manual mode, ensure relays reflect manual state
    if (!enabled) {
        // In manual mode, we don't change relay states here - they're controlled manually
    }
}

bool AutomationController::isAutoMode() const {
    return autoModeEnabled;
}

void AutomationController::controlFan(float temperature) {
    bool shouldBeOn = temperature > temperatureThreshold;
    bool shouldBeOff = temperature < (temperatureThreshold - hysteresis);
    
    if (shouldBeOn && !fanState) {
        relayController.setFan(true);
        fanState = true;
    } else if (shouldBeOff && fanState) {
        relayController.setFan(false);
        fanState = false;
    }
    // Otherwise keep current state (hysteresis prevents rapid switching)
}

void AutomationController::controlLight(uint8_t lightLevel, bool motionDetected) {
    bool isDark = lightLevel < lightThreshold;
    bool motionRecently = (millis() - lastMotionTime) < motionTimeout;
    
    bool shouldBeOn = isDark && motionRecently;
    
    if (shouldBeOn && !lightState) {
        relayController.setLight(true);
        lightState = true;
    } else if (!shouldBeOn && lightState) {
        relayController.setLight(false);
        lightState = false;
    }
}

void AutomationController::controlBuzzer(float airQuality) {
    bool shouldBeOn = airQuality > airQualityThreshold;
    
    if (shouldBeOn) {
        relayController.setBuzzer(true);
    } else {
        relayController.setBuzzer(false);
    }
}