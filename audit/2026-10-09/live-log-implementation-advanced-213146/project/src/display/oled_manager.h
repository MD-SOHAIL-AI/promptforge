/**
 * @file oled_manager.h
 * @brief OLED display manager using I2C
 */

#ifndef OLED_MANAGER_H
#define OLED_MANAGER_H

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

class OledManager {
public:
    OledManager(uint8_t width = 128, uint8_t height = 64);
    ~OledManager();
    
    bool begin();
    
    // Display methods
    void clearDisplay();
    void display();
    
    // Sensor data display
    void displaySensorData(float temperature, float humidity, 
                          float airQuality, uint16_t lightLevel, 
                          bool motionDetected);
    
    // System status display
    void displaySystemStatus(bool wifiConnected, bool autoMode, 
                           bool fanOn, bool lightOn, bool buzzerOn);
    
    // Alert/error display
    void displayAlert(const String& message);
    void displayError(const String& message);
    
private:
    Adafruit_SSD1306* display_;
    uint8_t screenWidth;
    uint8_t screenHeight;
};

#endif // OLED_MANAGER_H