/**
 * @file relay_controller.cpp
 * @brief Relay controller implementation for managing output devices
 */

#include "relay_controller.h"

RelayController::RelayController(uint8_t fanPin, uint8_t lightPin, uint8_t buzzerPin)
    : fanPin(fanPin),
      lightPin(lightPin),
      buzzerPin(buzzerPin),
      fanState(false),
      lightState(false),
      buzzerState(false) {}

RelayController::~RelayController() {}

bool RelayController::begin() {
    // Set pin modes
    pinMode(fanPin, OUTPUT);
    pinMode(lightPin, OUTPUT);
    pinMode(buzzerPin, OUTPUT);
    
    // Initialize outputs to off state
    digitalWrite(fanPin, LOW);
    digitalWrite(lightPin, LOW);
    digitalWrite(buzzerPin, LOW);
    
    return true;
}

void RelayController::setFan(bool state) {
    if (fanState != state) {
        fanState = state;
        digitalWrite(fanPin, fanState ? HIGH : LOW);
    }
}

void RelayController::setLight(bool state) {
    if (lightState != state) {
        lightState = state;
        digitalWrite(lightPin, lightState ? HIGH : LOW);
    }
}

void RelayController::setBuzzer(bool state) {
    if (buzzerState != state) {
        buzzerState = state;
        digitalWrite(buzzerPin, buzzerState ? HIGH : LOW);
    }
}

bool RelayController::getFanState() const {
    return fanState;
}

bool RelayController::getLightState() const {
    return lightState;
}

bool RelayController::getBuzzerState() const {
    return buzzerState;
}

String RelayController::getStatusString() const {
    return String("Fan: ") + (fanState ? "ON" : "OFF") + 
           ", Light: " + (lightState ? "ON" : "OFF") + 
           ", Buzzer: " + (buzzerState ? "ON" : "OFF");
}