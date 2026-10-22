/**
 * @file diagnostics.h
 * @brief System diagnostics and error logging
 */

#ifndef DIAGNOSTICS_H
#define DIAGNOSTICS_H

#include <Arduino.h>
#include "storage/settings_manager.h"

class Diagnostics {
public:
    Diagnostics(SettingsManager& settings);
    ~Diagnostics();
    
    bool begin();
    void logEvent(const String& event, const String& details = "");
    void logError(const String& error, const String& details = "");
    void logSensorError(const String& sensorName, const String& error);
    void logRelayChange(const String& relay, bool state);
    void logWiFiEvent(const String& event);
    void logSystemStartup();
    
private:
    SettingsManager& settingsManager;
    
    String getTimestamp();
    void writeToLog(const String& logEntry);
};

#endif // DIAGNOSTICS_H