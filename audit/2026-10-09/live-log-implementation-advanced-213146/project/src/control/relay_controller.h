/**
 * @file relay_controller.h
 * @brief Relay controller for managing output devices
 */

#ifndef RELAY_CONTROLLER_H
#define RELAY_CONTROLLER_H

#include <Arduino.h>

class RelayController {
public:
    RelayController(uint8_t fanPin, uint8_t lightPin, uint8_t buzzerPin);
    ~RelayController();
    
    bool begin();
    
    // Control methods
    void setFan(bool state);
    void setLight(bool state);
    void setBuzzer(bool state);
    
    // Status methods
    bool getFanState() const;
    bool getLightState() const;
    bool getBuzzerState() const;
    String getStatusString() const;
    
private:
    uint8_t fanPin;
    uint8_t lightPin;
    uint8_t buzzerPin;
    
    bool fanState;
    bool lightState;
    bool buzzerState;
};

#endif // RELAY_CONTROLLER_H