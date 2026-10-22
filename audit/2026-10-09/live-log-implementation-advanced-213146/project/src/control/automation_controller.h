/**
 * @file automation_controller.h
 * @brief Automation controller for environmental control logic
 */

#ifndef AUTOMATION_CONTROLLER_H
#define AUTOMATION_CONTROLLER_H

#include <Arduino.h>
#include "../sensors/temperature_sensor.h"
#include "../sensors/air_quality_sensor.h"
#include "../sensors/light_sensor.h"
#include "../sensors/motion_sensor.h"
#include "../control/relay_controller.h"

class AutomationController {
public:
    AutomationController(
        TemperatureSensor& tempSensor,
        AirQualitySensor& airQualitySensor,
        LightSensor& lightSensor,
        MotionSensor& motionSensor,
        RelayController& relayController
    );
    ~AutomationController();
    
    void begin();
    void update();
    
    // Configuration setters
    void setTemperatureThreshold(float threshold);
    void setAirQualityThreshold(float threshold);
    void setLightThreshold(uint8_t threshold);
    void setMotionTimeout(unsigned long timeout);
    void setHysteresis(float hysteresis);
    
    // Mode control
    void setAutoMode(bool enabled);
    bool isAutoMode() const;
    
private:
    TemperatureSensor& temperatureSensor;
    AirQualitySensor& airQualitySensor;
    LightSensor& lightSensor;
    MotionSensor& motionSensor;
    RelayController& relayController;
    
    // Configuration
    float temperatureThreshold;
    float airQualityThreshold;
    uint8_t lightThreshold;
    unsigned long motionTimeout;
    float hysteresis;
    
    bool autoModeEnabled;
    
    // State tracking
    unsigned long lastMotionTime;
    bool fanState;
    bool lightState;
    
    // Internal methods
    void controlFan(float temperature);
    void controlLight(uint8_t lightLevel, bool motionDetected);
    void controlBuzzer(float airQuality);
};

#endif // AUTOMATION_CONTROLLER_H