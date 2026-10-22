/**
 * @file wifi_manager.h
 * @brief WiFi manager for ESP32 with AP/STA modes and auto-reconnect
 */

#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>

class WifiManager {
public:
    WifiManager();
    ~WifiManager();
    
    bool begin();
    void update();
    
    // Connection methods
    bool connectToAP(const char* ssid, const char* password);
    void startAPMode(const char* apSSID, const char* apPassword);
    void stopAPMode();
    
    // Status methods
    bool isConnected() const;
    bool isInAPMode() const;
    String getLocalIP() const;
    String getSSID() const;
    int getRSSI() const;
    
    // Configuration
    void saveWiFiCredentials(const char* ssid, const char* password);
    bool loadWiFiCredentials(char* ssid, size_t ssidSize, char* password, size_t passSize);
    void clearWiFiCredentials();
    
private:
    Preferences preferences;
    bool apModeActive;
    unsigned long lastConnectionAttempt;
    const unsigned long connectionInterval = 10000; // 10 seconds
    String storedSSID;
    String storedPassword;
    
    void attemptConnection();
};

#endif // WIFI_MANAGER_H