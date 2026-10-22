/**
 * @file main.cpp
 * @brief Main entry point for the ESP32 Smart Environment Monitoring and Control System
 * @version 1.0
 * @date 2024
 * 
 * This is the main application file that initializes all system components
 * and manages the overall system state machine.
 */

#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <ESPAsyncWebServer.h>
#include <AsyncWebSocket.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_Sensor.h>
#include <DHT.h>
#include <ArduinoJson.h>

// Include our custom modules
#include "sensors/temperature_sensor.h"
#include "sensors/air_quality_sensor.h"
#include "sensors/light_sensor.h"
#include "sensors/motion_sensor.h"
#include "control/automation_controller.h"
#include "control/relay_controller.h"
#include "network/wifi_manager.h"
#include "network/web_server.h"
#include "network/websocket_manager.h"
#include "storage/settings_manager.h"
#include "storage/data_logger.h"
#include "display/oled_manager.h"
#include "system/diagnostics.h"
#include "system/watchdog.h"

// System states
enum class SystemState {
    BOOT,
    INITIALIZATION,
    WIFI_CONNECTING,
    NORMAL_OPERATION,
    ERROR_RECOVERY
};

// Global system state
SystemState currentState = SystemState::BOOT;
Preferences preferences;

// Forward declarations
void setup();
void loop();
void updateSystemState();
void handleSystemEvents();

#endif // MAIN_H