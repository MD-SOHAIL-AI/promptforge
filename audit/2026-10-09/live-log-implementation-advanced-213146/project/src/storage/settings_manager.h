/**
 * @file settings_manager.h
 * @brief Manages persistent storage of system settings using ESP32 Preferences/NVS
 */

#ifndef SETTINGS_MANAGER_H
#define SETTINGS_MANAGER_H

#include <Arduino.h>
#include <Preferences.h>

/**
 * @struct SystemSettings
 * @brief Structure to hold system configuration settings
 */
struct SystemSettings {
    float temperatureThreshold;  ///< Temperature threshold for fan control (°C)
    float airQualityThreshold;   ///< Air quality threshold for buzzer alert (PPM)
    uint8_t lightThreshold;      ///< Light threshold for automatic lighting (0-255)
    unsigned long motionTimeout; ///< Motion timeout for light control (ms)
    float hysteresis;            ///< Hysteresis for relay control
    bool autoModeEnabled;        ///< Auto mode enabled flag
    
    // WiFi credentials
    char wifiSSID[32];
    char wifiPassword[64];
    
    // Constructor with default values
    SystemSettings() : 
        temperatureThreshold(25.0), 
        airQualityThreshold(1000.0), 
        lightThreshold(100), 
        motionTimeout(30000), 
        hysteresis(2.0), 
        autoModeEnabled(true) {
        memset(wifiSSID, 0, sizeof(wifiSSID));
        memset(wifiPassword, 0, sizeof(wifiPassword));
    }
};

class SettingsManager {
public:
    SettingsManager();
    ~SettingsManager();
    
    bool begin();
    bool loadSettings(SystemSettings& settings);
    bool saveSettings(const SystemSettings& settings);
    bool resetToDefaults();
    
    // WiFi specific methods
    bool loadWiFiCredentials(char* ssid, size_t ssidSize, char* password, size_t passSize);
    bool saveWiFiCredentials(const char* ssid, const char* password);
    bool hasWiFiCredentials() const;
    void clearWiFiCredentials();
    
private:
    Preferences preferences;
    static constexpr const char* NAMESPACE = "env_settings";
    static constexpr const char* KEY_SETTINGS = "settings";
    static constexpr const char* KEY_WIFI_SSID = "wifi_ssid";
    static constexpr const char* KEY_WIFI_PASSWORD = "wifi_password";
};

#endif // SETTINGS_MANAGER_H