/**
 * @file sensor_data.h
 * @brief Sensor data structure definition
 */

#ifndef SENSOR_DATA_H
#define SENSOR_DATA_H

#include <Arduino.h>

/**
 * @struct SensorData
 * @brief Structure to hold all sensor readings
 */
struct SensorData {
    float temperature;    ///< Temperature in Celsius
    float humidity;       ///< Humidity in percentage
    float airQuality;     ///< Air quality in PPM
    uint16_t lightLevel;  ///< Light level (0-4095)
    bool motionDetected;  ///< Motion detection status
    unsigned long timestamp; ///< Timestamp of reading
    
    // Constructor
    SensorData() : 
        temperature(0.0), 
        humidity(0.0), 
        airQuality(0.0), 
        lightLevel(0), 
        motionDetected(false), 
        timestamp(0) {}
    
    // Parameterized constructor
    SensorData(float temp, float hum, float airQual, uint16_t light, bool motion, unsigned long time)
        : temperature(temp), humidity(hum), airQuality(airQual), lightLevel(light), 
          motionDetected(motion), timestamp(time) {}
};

#endif // SENSOR_DATA_H