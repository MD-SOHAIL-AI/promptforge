#include "buzzer.h"
Buzzer::Buzzer(int pin) : _pin(pin), _active(false), _high(false), _remaining(0), _changedAt(0), _duration(0) {}
void Buzzer::begin() { pinMode(_pin, OUTPUT); off(); }
void Buzzer::start(unsigned int count, unsigned long duration) {
    _remaining = count; _duration = duration; _changedAt = millis();
    _active = true; _high = true; digitalWrite(_pin, HIGH);
}
void Buzzer::beep(int duration) { if (duration > 0) start(1, duration); }
void Buzzer::doubleBeep(int duration) { if (duration > 0) start(2, duration); }
void Buzzer::errorBeep() { beep(200); }
void Buzzer::confirmationBeep() { beep(100); }
void Buzzer::off() { _active = false; _high = false; _remaining = 0; digitalWrite(_pin, LOW); }
void Buzzer::update() {
    if (!_active || millis() - _changedAt < (_high ? _duration : 100UL)) return;
    _changedAt = millis();
    if (_high) { digitalWrite(_pin, LOW); _high = false; if (--_remaining == 0) _active = false; }
    else { _high = true; digitalWrite(_pin, HIGH); }
}
