/**
 * @file websocket_manager.h
 * @brief WebSocket manager for real-time communication with dashboard
 */

#ifndef WEBSOCKET_MANAGER_H
#define WEBSOCKET_MANAGER_H

#include <Arduino.h>
#include <WebSocketsServer.h>
#include <JSON.hpp>
#include "sensors/sensor_data.h"
#include "control/relay_controller.h"

class WebsocketManager {
public:
    WebsocketManager();
    ~WebsocketManager();
    
    bool begin(uint16_t port = 81);
    void update();
    void broadcastSensorData(const SensorData& data);
    void broadcastSystemStatus(const String& status);
    void broadcastRelayStatus(bool fanState, bool lightState);
    
    // Callback for incoming WebSocket messages
    void onMessage(std::function<void(uint8_t num, const String& message)> callback);
    
private:
    WebSocketsServer webSocket;
    std::function<void(uint8_t num, const String& message)> messageCallback;
    
    void handleWebSocketEvent(uint8_t num, WStype_t type, uint8_t * payload, size_t length);
    String createJSONResponse(const SensorData& data);
    String createStatusJSON(const String& status);
    String createRelayStatusJSON(bool fanState, bool lightState);
};

#endif // WEBSOCKET_MANAGER_H