/**
 * @file temperature_sensor.h
 * @brief Temperature and humidity sensor interface using DHT22
 */

#ifndef TEMPERATURE_SENSOR_H
#define TEMPERATURE_SENSOR_H

#include <Arduino.h>
#include <DHT.h>

class TemperatureSensor {
public:
    TemperatureSensor(uint8_t pin);
    ~TemperatureSensor();
    
    bool begin();
    bool readSensor(float& temperature, float& humidity);
    bool isAvailable() const;
    String getStatusString() const;
    
private:
    DHT* dht;
    uint8_t pin;
    bool sensorInitialized;
    unsigned long lastReadTime;
    static constexpr unsigned long READ_INTERVAL = 2000; // 2 seconds
};

#endif // TEMPERATURE_SENSOR_H