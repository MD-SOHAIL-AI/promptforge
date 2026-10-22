/**
 * @file air_quality_sensor.h
 * @brief Air quality sensor interface using MQ-135
 */

#ifndef AIR_QUALITY_SENSOR_H
#define AIR_QUALITY_SENSOR_H

#include <Arduino.h>

class AirQualitySensor {
public:
    AirQualitySensor(uint8_t pin);
    ~AirQualitySensor();
    
    bool begin();
    bool readSensor(float& airQuality);
    bool isAvailable() const;
    String getStatusString() const;
    float getPPM() const;
    
private:
    uint8_t pin;
    bool sensorInitialized;
    unsigned long lastReadTime;
    static constexpr unsigned long READ_INTERVAL = 1000; // 1 second
    float lastPPM;
};

#endif // AIR_QUALITY_SENSOR_H