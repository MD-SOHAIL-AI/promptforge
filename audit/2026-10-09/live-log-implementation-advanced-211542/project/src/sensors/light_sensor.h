/**
 * @file light_sensor.h
 * @brief Light sensor interface using LDR (Light Dependent Resistor)
 */

#ifndef LIGHT_SENSOR_H
#define LIGHT_SENSOR_H

#include <Arduino.h>

class LightSensor {
public:
    LightSensor(uint8_t pin);
    ~LightSensor();
    
    bool begin();
    bool readSensor(float& lightLevel);
    bool isAvailable() const;
    String getStatusString() const;
    uint16_t getRawValue() const;
    
private:
    uint8_t pin;
    bool sensorInitialized;
    unsigned long lastReadTime;
    static constexpr unsigned long READ_INTERVAL = 500; // 500ms
    uint16_t lastRawValue;
};

#endif // LIGHT_SENSOR_H