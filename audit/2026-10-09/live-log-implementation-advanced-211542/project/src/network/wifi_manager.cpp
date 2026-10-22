/**
 * @file wifi_manager.cpp
 * @brief WiFi manager implementation for ESP32
 */

#include "wifi_manager.h"

WifiManager::WifiManager() 
    : apModeActive(false), 
      lastConnectionAttempt(0) {}

WifiManager::~WifiManager() {
    if (apModeActive) {
        stopAPMode();
    }
}

bool WifiManager::begin() {
    // Initialize NVS for storing credentials
    preferences.begin("wifi-config", false);
    
    // Try to load stored credentials
    char ssid[32] = {0};
    char password[64] = {0};
    
    if (loadWiFiCredentials(ssid, sizeof(ssid), password, sizeof(password))) {
        // Attempt to connect with stored credentials
        return connectToAP(ssid, password);
    }
    
    // No stored credentials, start in AP mode for configuration
    startAPMode("EnvMonitor-Setup", "password123");
    return true;
}

void WifiManager::update() {
    // Handle automatic reconnection
    if (!isConnected() && !isInAPMode()) {
        unsigned long now = millis();
        if (now - lastConnectionAttempt >= connectionInterval) {
            attemptConnection();
            lastConnectionAttempt = now;
        }
    }
    
    // Handle WiFi events
    if (WiFi.getMode() == WIFI_MODE_STA && !isConnected()) {
        // Connection lost, try to reconnect
        attemptConnection();
    }
}

bool WifiManager::connectToAP(const char* ssid, const char* password) {
    if (!ssid || !*ssid) {
        return false;
    }
    
    // Disconnect any existing connection
    WiFi.disconnect(true);
    delay(100);
    
    // Set WiFi to station mode and connect
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);
    
    // Store credentials for future use
    saveWiFiCredentials(ssid, password);
    
    storedSSID = ssid;
    storedPassword = password;
    
    // Wait for connection with timeout
    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && 
           millis() - startAttemptTime < 15000) { // 15 second timeout
        delay(500);
    }
    
    return WiFi.status() == WL_CONNECTED;
}

void WifiManager::startAPMode(const char* apSSID, const char* apPassword) {
    if (apModeActive) {
        return; // Already in AP mode
    }
    
    WiFi.mode(WIFI_AP);
    WiFi.softAP(apSSID, apPassword);
    apModeActive = true;
    
    // Delay to let AP start
    delay(100);
}

void WifiManager::stopAPMode() {
    if (!apModeActive) {
        return;
    }
    
    WiFi.softAPdisconnect(true);
    apModeActive = false;
}

bool WifiManager::isConnected() const {
    return WiFi.status() == WL_CONNECTED && !apModeActive;
}

bool WifiManager::isInAPMode() const {
    return apModeActive;
}

String WifiManager::getLocalIP() const {
    if (isConnected()) {
        return WiFi.localIP().toString();
    } else if (isInAPMode()) {
        return WiFi.softAPIP().toString();
    }
    return "0.0.0.0";
}

String WifiManager::getSSID() const {
    if (isConnected()) {
        return WiFi.SSID();
    } else if (isInAPMode()) {
        return WiFi.softAPSSID();
    }
    return "";
}

int WifiManager::getRSSI() const {
    if (isConnected()) {
        return WiFi.RSSI();
    }
    return 0;
}

void WifiManager::saveWiFiCredentials(const char* ssid, const char* password) {
    preferences.putString("ssid", ssid);
    preferences.putString("password", password);
}

bool WifiManager::loadWiFiCredentials(char* ssid, size_t ssidSize, char* password, size_t passSize) {
    String storedSSIDStr = preferences.getString("ssid", "");
    String storedPasswordStr = preferences.getString("password", "");
    
    if (storedSSIDStr.length() > 0 && storedPasswordStr.length() > 0) {
        strncpy(ssid, storedSSIDStr.c_str(), ssidSize - 1);
        ssid[ssidSize - 1] = '\0';
        
        strncpy(password, storedPasswordStr.c_str(), passSize - 1);
        password[passSize - 1] = '\0';
        
        return true;
    }
    
    return false;
}

void WifiManager::clearWiFiCredentials() {
    preferences.clear();
    storedSSID = "";
    storedPassword = "";
}

void WifiManager::attemptConnection() {
    if (storedSSID.length() == 0 || storedPassword.length() == 0) {
        // No credentials stored, start AP mode
        startAPMode("EnvMonitor-Setup", "password123");
        return;
    }
    
    // Try to connect with stored credentials
    connectToAP(storedSSID.c_str(), storedPassword.c_str());
}