/**
 * @file oled_manager.cpp
 * @brief OLED display manager implementation using I2C
 */

#include "oled_manager.h"

OledManager::OledManager(uint8_t width, uint8_t height)
    : screenWidth(width),
      screenHeight(height),
      display(nullptr) {}

OledManager::~OledManager() {
    if (display) {
        delete display;
        display = nullptr;
    }
}

bool OledManager::begin() {
    // Initialize display
    display = new Adafruit_SSD1306(screenWidth, screenHeight, &Wire, -1);
    if (!display) {
        return false;
    }
    
    // SSD1306_SWITCHCAPVCC = generate display voltage from 3.3V internally
    if (!display->begin(SSD1306_SWITCHCAPVCC, 0x3C)) { // Address 0x3C for 128x64
        delete display;
        display = nullptr;
        return false;
    }
    
    // Clear the buffer
    display->clearDisplay();
    display->display();
    
    // Set text size and color
    display->setTextSize(1);
    display->setTextColor(SSD1306_WHITE);
    
    return true;
}

void OledManager::clearDisplay() {
    if (display_) {
        display_->clearDisplay();
    }
}

void OledManager::display() {
    if (display_) {
        display_->display();
    }
}

void OledManager::displaySensorData(float temperature, float humidity, 
                                   float airQuality, uint16_t lightLevel, 
                                   bool motionDetected) {
    if (!display) return;
    
    display->clearDisplay();
    
    // Display header
    display->setCursor(0, 0);
    display->println("Environment Monitor");
    display->println("-------------------");
    
    // Temperature
    display->setCursor(0, 16);
    display->print("Temp: ");
    display->print(temperature, 1);
    display->print(" C");
    
    // Humidity
    display->setCursor(0, 24);
    display->print("Hum: ");
    display->print(humidity, 1);
    display->print(" %");
    
    // Air Quality
    display->setCursor(0, 32);
    display->print("AirQ: ");
    display->print(airQuality, 0);
    display->print(" PPM");
    
    // Light Level
    display->setCursor(0, 40);
    display->print("Light: ");
    display->print(lightLevel);
    
    // Motion
    display->setCursor(0, 48);
    display->print("Motion: ");
    display->print(motionDetected ? "YES" : "NO");
    
    display->display();
}

void OledManager::displaySystemStatus(bool wifiConnected, bool autoMode, 
                                    bool fanOn, bool lightOn, bool buzzerOn) {
    if (!display) return;
    
    display->clearDisplay();
    
    // Display header
    display->setCursor(0, 0);
    display->println("System Status");
    display->println("-------------");
    
    // WiFi status
    display->setCursor(0, 16);
    display->print("WiFi: ");
    display->println(wifiConnected ? "Connected" : "Disconnected");
    
    // Mode
    display->setCursor(0, 24);
    display->print("Mode: ");
    display->println(autoMode ? "AUTO" : "MANUAL");
    
    // Relays
    display->setCursor(0, 32);
    display->print("Fan: ");
    display->println(fanOn ? "ON" : "OFF");
    
    display->setCursor(0, 40);
    display->print("Light: ");
    display->println(lightOn ? "ON" : "OFF");
    
    display->setCursor(0, 48);
    display->print("Buzzer: ");
    display->println(buzzerOn ? "ON" : "OFF");
    
    display->display();
}

void OledManager::displayAlert(const String& message) {
    if (!display) return;
    
    display->clearDisplay();
    display->setCursor(0, 0);
    display->setTextSize(2);
    display->println("ALERT");
    display->setTextSize(1);
    display->println("----------------");
    display->println(message);
    display->display();
}

void OledManager::displayError(const String& message) {
    if (!display) return;
    
    display->clearDisplay();
    display->setCursor(0, 0);
    display->setTextSize(2);
    display->println("ERROR");
    display->setTextSize(1);
    display->println("----------------");
    display->println(message);
    display->display();
}