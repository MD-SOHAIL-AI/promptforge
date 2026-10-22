/**
 * @file main.cpp
 * @brief Main entry point for ESP32 Smart Environment Monitoring and Control System
 * @author Forge Agent V3
 * @version 1.0.0
 */

#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <ESPAsyncWebServer.h>
#include <AsyncTCP.h>
#include <ArduinoJson.h>
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

// FreeRTOS handles
TaskHandle_t sensorTaskHandle = nullptr;
TaskHandle_t controlTaskHandle = nullptr;
TaskHandle_t displayTaskHandle = nullptr;
TaskHandle_t networkTaskHandle = nullptr;
TaskHandle_t loggingTaskHandle = nullptr;
TaskHandle_t monitoringTaskHandle = nullptr;

// Shared data structures
struct SensorData {
    float temperature;
    float humidity;
    float airQuality;
    float lightLevel;
    bool motionDetected;
    unsigned long timestamp;
};

struct ControlState {
    bool fanOn;
    bool lightOn;
    bool buzzerOn;
    bool autoMode;
    bool wifiConnected;
};

SensorData sensorData;
ControlState controlState;

// Mutexes for shared data protection
SemaphoreHandle_t sensorDataMutex;
SemaphoreHandle_t controlStateMutex;
SemaphoreHandle_t settingsMutex;

// Queues for inter-task communication
QueueHandle_t sensorDataQueue;
QueueHandle_t controlCommandQueue;

// Forward declarations
void sensorAcquisitionTask(void *parameter);
void controlLogicTask(void *parameter);
void oledUpdateTask(void *parameter);
void networkTask(void *parameter);
void dataLoggingTask(void *parameter);
void systemMonitoringTask(void *parameter);
void stateMachineTask(void *parameter);

// Global instances
TemperatureSensor tempSensor;
AirQualitySensor airQualitySensor;
LightSensor lightSensor;
MotionSensor motionSensor;
AutomationController automationController;
RelayController relayController;
WiFiManager wifiManager;
WebServer webServer;
WebSocketManager websocketManager;
SettingsManager settingsManager;
DataLogger dataLogger;
OledManager oledManager;
Diagnostics diagnostics;
Watchdog watchdog;

void setup() {
    Serial.begin(115200);
    delay(1000);
    
    Serial.println("ESP32 Smart Environment Monitoring System");
    Serial.println("Initializing...");
    
    // Initialize mutexes
    sensorDataMutex = xSemaphoreCreateMutex();
    controlStateMutex = xSemaphoreCreateMutex();
    settingsMutex = xSemaphoreCreateMutex();
    
    // Initialize queues
    sensorDataQueue = xQueueCreate(10, sizeof(SensorData));
    controlCommandQueue = xQueueCreate(10, sizeof(ControlState));
    
    // Initialize modules
    if (!settingsManager.begin()) {
        Serial.println("Failed to initialize settings manager");
    }
    
    if (!tempSensor.begin()) {
        Serial.println("Failed to initialize temperature sensor");
    }
    
    if (!airQualitySensor.begin()) {
        Serial.println("Failed to initialize air quality sensor");
    }
    
    if (!lightSensor.begin()) {
        Serial.println("Failed to initialize light sensor");
    }
    
    if (!motionSensor.begin()) {
        Serial.println("Failed to initialize motion sensor");
    }
    
    if (!relayController.begin()) {
        Serial.println("Failed to initialize relay controller");
    }
    
    if (!oledManager.begin()) {
        Serial.println("Failed to initialize OLED display");
    }
    
    if (!diagnostics.begin()) {
        Serial.println("Failed to initialize diagnostics");
    }
    
    if (!watchdog.begin()) {
        Serial.println("Failed to initialize watchdog");
    }
    
    // Load settings
    settingsManager.loadSettings();
    
    // Create FreeRTOS tasks
    xTaskCreatePinnedToCore(
        sensorAcquisitionTask, 
        "SensorTask", 
        4096, 
        nullptr, 
        2, 
        &sensorTaskHandle, 
        0
    );
    
    xTaskCreatePinnedToCore(
        controlLogicTask, 
        "ControlTask", 
        4096, 
        nullptr, 
        2, 
        &controlTaskHandle, 
        0
    );
    
    xTaskCreatePinnedToCore(
        oledUpdateTask, 
        "DisplayTask", 
        4096, 
        nullptr, 
        1, 
        &displayTaskHandle, 
        0
    );
    
    xTaskCreatePinnedToCore(
        networkTask, 
        "NetworkTask", 
        8192, 
        nullptr, 
        1, 
        &networkTaskHandle, 
        1
    );
    
    xTaskCreatePinnedToCore(
        dataLoggingTask, 
        "LoggingTask", 
        4096, 
        nullptr, 
        1, 
        &loggingTaskHandle, 
        0
    );
    
    xTaskCreatePinnedToCore(
        systemMonitoringTask, 
        "MonitorTask", 
        4096, 
        nullptr, 
        2, 
        &monitoringTaskHandle, 
        0
    );
    
    xTaskCreatePinnedToCore(
        stateMachineTask, 
        "StateMachineTask", 
        4096, 
        nullptr, 
        3, 
        nullptr, 
        0
    );
    
    Serial.println("System initialized successfully");
}

void loop() {
    // Main loop is intentionally empty - all work done in FreeRTOS tasks
    // Just feed the watchdog periodically
    watchdog.feed();
    delay(100);
}

void sensorAcquisitionTask(void *parameter) {
    const TickType_t xFrequency = pdMS_TO_TICKS(1000); // 1Hz sensor reading
    TickType_t xLastWakeTime = xTaskGetTickCount();
    
    for (;;) {
        // Read all sensors
        SensorData data;
        data.temperature = tempSensor.readTemperature();
        data.humidity = tempSensor.readHumidity();
        data.airQuality = airQualitySensor.readAirQuality();
        data.lightLevel = lightSensor.readLightLevel();
        data.motionDetected = motionSensor.readMotion();
        data.timestamp = millis();
        
        // Validate sensor readings
        if (!tempSensor.isValid()) {
            diagnostics.logError("Temperature sensor failure");
            data.temperature = NAN;
            data.humidity = NAN;
        }
        
        if (!airQualitySensor.isValid()) {
            diagnostics.logError("Air quality sensor failure");
            data.airQuality = NAN;
        }
        
        if (!lightSensor.isValid()) {
            diagnostics.logError("Light sensor failure");
            data.lightLevel = NAN;
        }
        
        if (!motionSensor.isValid()) {
            diagnostics.logError("Motion sensor failure");
            data.motionDetected = false;
        }
        
        // Send data to queue
        if (xQueueSend(sensorDataQueue, &data, 0) != pdTRUE) {
            diagnostics.logWarning("Sensor data queue full");
        }
        
        // Update global sensor data (with mutex protection)
        if (xSemaphoreTake(sensorDataMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            sensorData = data;
            xSemaphoreGive(sensorDataMutex);
        }
        
        // Wait until next cycle
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void controlLogicTask(void *parameter) {
    const TickType_t xFrequency = pdMS_TO_TICKS(500); // 2Hz control logic
    TickType_t xLastWakeTime = xTaskGetTickCount();
    
    for (;;) {
        // Get latest sensor data
        SensorData currentData;
        if (xSemaphoreTake(sensorDataMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            currentData = sensorData;
            xSemaphoreGive(sensorDataMutex);
        }
        
        // Get current control state
        if (xSemaphoreTake(controlStateMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            // Update automation controller with sensor data
            automationController.updateSensorData(currentData);
            
            // Get control decisions from automation controller
            ControlState newState = automationController.getControlState();
            
            // Apply hysteresis and safety logic
            relayController.setFanState(newState.fanOn);
            relayController.setLightState(newState.lightOn);
            relayController.setBuzzerState(newState.buzzerOn);
            
            // Update global control state
            controlState = newState;
            xSemaphoreGive(controlStateMutex);
        }
        
        // Check for manual override commands
        ControlState command;
        if (xQueueReceive(controlCommandQueue, &command, 0) == pdTRUE) {
            if (xSemaphoreTake(controlStateMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
                controlState.autoMode = command.autoMode;
                if (!command.autoMode) {
                    // Manual mode - apply direct commands
                    relayController.setFanState(command.fanOn);
                    relayController.setLightState(command.lightOn);
                    relayController.setBuzzerState(command.buzzerOn);
                }
                xSemaphoreGive(controlStateMutex);
            }
        }
        
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void oledUpdateTask(void *parameter) {
    const TickType_t xFrequency = pdMS_TO_TICKS(500); // 2Hz display update
    TickType_t xLastWakeTime = xTaskGetTickCount();
    
    for (;;) {
        // Get current data for display
        SensorData displayData;
        ControlState displayState;
        
        if (xSemaphoreTake(sensorDataMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            displayData = sensorData;
            xSemaphoreGive(sensorDataMutex);
        }
        
        if (xSemaphoreTake(controlStateMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            displayState = controlState;
            xSemaphoreGive(controlStateMutex);
        }
        
        // Update OLED display
        oledManager.displaySensorData(
            displayData.temperature,
            displayData.humidity,
            displayData.airQuality,
            displayData.lightLevel,
            displayData.motionDetected
        );
        
        oledManager.displaySystemStatus(
            displayState.wifiConnected,
            displayState.autoMode,
            displayState.fanOn,
            displayState.lightOn,
            displayState.buzzerOn
        );
        
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void networkTask(void *parameter) {
    for (;;) {
        // Handle WiFi connection
        if (!wifiManager.isConnected()) {
            wifiManager.connect();
            currentState = SystemState::WIFI_CONNECTING;
        } else {
            currentState = SystemState::NORMAL_OPERATION;
        }
        
        // Handle web server and websockets
        webServer.handleClient();
        websocketManager.handleClients();
        
        // Update wifi status in control state
        if (xSemaphoreTake(controlStateMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            controlState.wifiConnected = wifiManager.isConnected();
            xSemaphoreGive(controlStateMutex);
        }
        
        vTaskDelay(pdMS_TO_TICKS(100)); // 10Hz network handling
    }
}

void dataLoggingTask(void *parameter) {
    const TickType_t xFrequency = pdMS_TO_TICKS(5000); // Log every 5 seconds
    TickType_t xLastWakeTime = xTaskGetTickCount();
    
    for (;;) {
        // Get current sensor data
        SensorData logData;
        if (xSemaphoreTake(sensorDataMutex, pdMS_TO_TICKS(10)) == pdTRUE) {
            logData = sensorData;
            xSemaphoreGive(sensorDataMutex);
        }
        
        // Log to SD card if available
        if (settingsManager.getDataLoggingEnabled()) {
            dataLogger.logSensorData(logData);
        }
        
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void systemMonitoringTask(void *parameter) {
    const TickType_t xFrequency = pdMS_TO_TICKS(10000); // Monitor every 10 seconds
    TickType_t xLastWakeTime = xTaskGetTickCount();
    
    for (;;) {
        // Perform system diagnostics
        diagnostics.checkSystemHealth();
        
        // Check for critical errors that require state change
        if (diagnostics.hasCriticalError()) {
            currentState = SystemState::ERROR_RECOVERY;
        } else if (currentState == SystemState::ERROR_RECOVERY && 
                   !diagnostics.hasCriticalError()) {
            currentState = SystemState::NORMAL_OPERATION;
        }
        
        // Report system stats
        diagnostics.reportStats();
        
        vTaskDelayUntil(&xLastWakeTime, xFrequency);
    }
}

void stateMachineTask(void *parameter) {
    for (;;) {
        switch (currentState) {
            case SystemState::BOOT:
                // Boot state - initialize hardware
                Serial.println("State: BOOT");
                currentState = SystemState::INITIALIZATION;
                break;
                
            case SystemState::INITIALIZATION:
                // Initialization state - initialize all modules
                Serial.println("State: INITIALIZATION");
                currentState = SystemState::WIFI_CONNECTING;
                break;
                
            case SystemState::WIFI_CONNECTING:
                // WiFi connecting state
                if (wifiManager.isConnected()) {
                    Serial.println("State: NORMAL_OPERATION (WiFi connected)");
                    currentState = SystemState::NORMAL_OPERATION;
                }
                break;
                
            case SystemState::NORMAL_OPERATION:
                // Normal operation - everything running
                // State handled by individual tasks
                break;
                
            case SystemState::ERROR_RECOVERY:
                // Error recovery state
                Serial.println("State: ERROR_RECOVERY");
                diagnostics.attemptRecovery();
                if (!diagnostics.hasCriticalError()) {
                    currentState = SystemState::NORMAL_OPERATION;
                }
                break;
        }
        
        vTaskDelay(pdMS_TO_TICKS(1000)); // Check state every second
    }
}