#pragma once
#include <Arduino.h>
class Buzzer {
public:
    explicit Buzzer(int pin);
    void begin();
    void beep(int duration);
    void doubleBeep(int duration);
    void errorBeep();
    void confirmationBeep();
    void update();
    void off();
private:
    int _pin;
    bool _active, _high;
    unsigned int _remaining;
    unsigned long _changedAt, _duration;
    void start(unsigned int count, unsigned long duration);
};
