#include "knock_detector.h"
KnockDetector::KnockDetector(int pin, int threshold, int debounceDelay)
    : _adcPin(pin), _threshold(threshold), _debounceDelay(debounceDelay > 0 ? debounceDelay : 1),
      _quietSince(0), _lastKnockTime(0), _armed(true), _rawHigh(false), _knockDetected(false) {}
void KnockDetector::begin() { pinMode(_adcPin, INPUT); reset(); }
bool KnockDetector::update() {
    const unsigned long now = millis();
    const bool high = analogRead(_adcPin) > _threshold;
    _knockDetected = false;
    if (high) {
        // Count even short pulses immediately. Ringing cannot rearm the detector
        // until the input has stayed below threshold for the full quiet period.
        if (_armed) { _armed = false; _lastKnockTime = now; _knockDetected = true; }
        _quietSince = now;
    } else {
        if (_rawHigh) _quietSince = now;
        if (now - _quietSince >= _debounceDelay) _armed = true;
    }
    _rawHigh = high;
    return _knockDetected;
}
bool KnockDetector::isKnockDetected() const { return _knockDetected; }
unsigned long KnockDetector::getLastKnockTime() const { return _lastKnockTime; }
void KnockDetector::reset() {
    _armed = true; _rawHigh = false; _knockDetected = false;
    _quietSince = millis(); _lastKnockTime = 0;
}
void KnockDetector::setThreshold(int threshold) { _threshold = threshold; }
int KnockDetector::getThreshold() const { return _threshold; }
