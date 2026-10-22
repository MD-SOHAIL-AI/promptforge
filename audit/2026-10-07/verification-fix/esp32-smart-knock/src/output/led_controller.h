#ifndef LED_CONTROLLER_H
#define LED_CONTROLLER_H

#include <Arduino.h>

class LedController {
public:
    LedController(int redPin, int greenPin, int bluePin);
    void begin();
    void setColor(uint8_t red, uint8_t green, uint8_t blue);
    void setColor(int color);  // Predefined colors
    void update();
    void flashGreen(int times, int duration);
    void flashRed(int times, int duration);
    void flashBlue(int times, int duration);
    void flashYellow(int times, int duration);
    void off();
    
    // Color definitions
    static const int COLOR_OFF = 0;
    static const int COLOR_RED = 1;
    static const int COLOR_GREEN = 2;
    static const int COLOR_BLUE = 3;
    static const int COLOR_YELLOW = 4;
    static const int COLOR_CYAN = 5;
    static const int COLOR_MAGENTA = 6;
    static const int COLOR_WHITE = 7;
    
private:
    int _redPin;
    int _greenPin;
    int _bluePin;
    bool _isFlashing;
    int _flashColor;
    int _flashCount;
    unsigned long _flashDuration;
    unsigned long _lastFlashTime;
    int _flashState;
    
    void updateFlash();
};

#endif // LED_CONTROLLER_H
