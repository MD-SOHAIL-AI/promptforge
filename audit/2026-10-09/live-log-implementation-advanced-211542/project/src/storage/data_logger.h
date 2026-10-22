/**
 * @file data_logger.h
 * @brief Handles logging sensor data to microSD card in CSV format
 */

#ifndef DATA_LOGGER_H
#define DATA_LOGGER_H

#include <Arduino.h>
#include <SD.h>
#include "sensors/sensor_data.h"

class DataLogger {
public:
    DataLogger(uint8_t csPin = SS);
    ~DataLogger();
    
    bool begin();
    bool logSensorData(const SensorData& data);
    bool isEnabled() const;
    void setEnabled(bool enabled);
    
private:
    uint8_t csPin;
    bool loggingEnabled;
    File dataFile;
    
    String getFilename();
    String formatCSVHeader();
    String formatCSVRow(const SensorData& data);
};

#endif // DATA_LOGGER_H