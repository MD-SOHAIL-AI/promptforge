/**
 * @file motion_sensor.h
 * @brief Motion sensor interface using PIR sensor
 */

#ifndef MOTION_SENSOR_H
#define MOTION_SENSOR_H

#include <Arduino.h>

class MotionSensor {
public:
    MotionSensor(uint8_t pin);
    ~MotionSensor();
    
    bool begin();
    bool readSensor(bool& motionDetected);
    bool isAvailable() const;
    String getStatusString() const;
    bool getLastMotionState() const;
    
private:
    uint8_t pin;
    bool sensorInitialized;
    unsigned long lastReadTime;
    static constexpr unsigned long READ_INTERVAL = 100; // 100ms
    bool lastMotionState;
};

#endif // MOTION_SENSOR_H