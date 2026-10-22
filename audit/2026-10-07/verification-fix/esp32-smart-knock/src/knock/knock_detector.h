#pragma once
#include <Arduino.h>
class KnockDetector {
public:
    KnockDetector(int adcPin, int threshold = 500, int debounceDelay = 50);
    void begin();
    bool update();
    bool isKnockDetected() const;
    unsigned long getLastKnockTime() const;
    void reset();
    void setThreshold(int threshold);
    int getThreshold() const;
private:
    int _adcPin, _threshold;
    unsigned long _debounceDelay, _quietSince, _lastKnockTime;
    bool _armed, _rawHigh, _knockDetected;
};
