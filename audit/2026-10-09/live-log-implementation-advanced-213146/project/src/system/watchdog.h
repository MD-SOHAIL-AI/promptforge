/**
 * @file watchdog.h
 * @brief Watchdog timer for system recovery
 */

#ifndef WATCHDOG_H
#define WATCHDOG_H

#include <Arduino.h>

class Watchdog {
public:
    Watchdog();
    ~Watchdog();
    
    bool begin(uint32_t timeoutMs = 8000);  // Default 8 second timeout
    void update();  // Call regularly to feed the watchdog
    void disable();
    
private:
    uint32_t timeoutMs;
    unsigned long lastFeed;
    bool enabled;
};

#endif // WATCHDOG_H