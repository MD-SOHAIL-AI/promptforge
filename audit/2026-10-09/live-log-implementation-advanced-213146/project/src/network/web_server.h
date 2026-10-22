/**
 * @file web_server.h
 * @brief Web server for hosting dashboard and handling HTTP requests
 */

#ifndef WEB_SERVER_H
#define WEB_SERVER_H

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include "sensors/sensor_data.h"
#include "storage/settings_manager.h"

class WebServer {
public:
    WebServer(SettingsManager& settings);
    ~WebServer();
    
    bool begin(uint16_t port = 80);
    void update();
    
    // Set callback for handling WebSocket messages from dashboard
    void setWebSocketCallback(std::function<void(const String& message)> callback);
    
private:
    AsyncWebServer server;
    SettingsManager& settingsManager;
    std::function<void(const String& message)> webSocketCallback;
    
    void setupRoutes();
    void handleRoot(AsyncWebServerRequest *request);
    void handleApiSensors(AsyncWebServerRequest *request);
    void handleApiStatus(AsyncWebServerRequest *request);
    void handleApiMode(AsyncWebServerRequest *request);
    void handleApiRelay(AsyncWebServerRequest *request);
    void handleApiSettings(AsyncWebServerRequest *request);
    void handleApiLogs(AsyncWebServerRequest *request);
    void handleNotFound(AsyncWebServerRequest *request);
    
    String getDashboardHTML();
    String getDashboardCSS();
    String getDashboardJS();
};

#endif // WEB_SERVER_H