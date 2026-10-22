#include "led_controller.h"

LedController::LedController(int redPin, int greenPin, int bluePin)
    : _redPin(redPin), _greenPin(greenPin), _bluePin(bluePin),
      _isFlashing(false), _flashColor(0), _flashCount(0), _flashDuration(0),
      _lastFlashTime(0), _flashState(0) {}

void LedController::begin() {
    pinMode(_redPin, OUTPUT);
    pinMode(_greenPin, OUTPUT);
    pinMode(_bluePin, OUTPUT);
    off();
}

void LedController::setColor(uint8_t red, uint8_t green, uint8_t blue) {
    analogWrite(_redPin, red);
    analogWrite(_greenPin, green);
    analogWrite(_bluePin, blue);
}

void LedController::setColor(int color) {
    _isFlashing = false;
    switch (color) {
        case COLOR_OFF:
            setColor(0, 0, 0);
            break;
        case COLOR_RED:
            setColor(255, 0, 0);
            break;
        case COLOR_GREEN:
            setColor(0, 255, 0);
            break;
        case COLOR_BLUE:
            setColor(0, 0, 255);
            break;
        case COLOR_YELLOW:
            setColor(255, 255, 0);
            break;
        case COLOR_CYAN:
            setColor(0, 255, 255);
            break;
        case COLOR_MAGENTA:
            setColor(255, 0, 255);
            break;
        case COLOR_WHITE:
            setColor(255, 255, 255);
            break;
        default:
            setColor(0, 0, 0);
    }
}

void LedController::update() {
    if (_isFlashing) {
        updateFlash();
    }
}

void LedController::flashGreen(int times, int duration) {
    _flashColor = COLOR_GREEN;
    _flashCount = times * 2;  // Each flash is on+off
    _flashDuration = duration;
    _lastFlashTime = millis();
    _flashState = 0;
    _isFlashing = true;
}

void LedController::flashRed(int times, int duration) {
    _flashColor = COLOR_RED;
    _flashCount = times * 2;
    _flashDuration = duration;
    _lastFlashTime = millis();
    _flashState = 0;
    _isFlashing = true;
}

void LedController::flashBlue(int times, int duration) {
    _flashColor = COLOR_BLUE;
    _flashCount = times * 2;
    _flashDuration = duration;
    _lastFlashTime = millis();
    _flashState = 0;
    _isFlashing = true;
}

void LedController::flashYellow(int times, int duration) {
    _flashColor = COLOR_YELLOW;
    _flashCount = times * 2;
    _flashDuration = duration;
    _lastFlashTime = millis();
    _flashState = 0;
    _isFlashing = true;
}

void LedController::off() {
    _isFlashing = false;
    setColor(0, 0, 0);
}

void LedController::updateFlash() {
    if (!_isFlashing) return;
    
    if (millis() - _lastFlashTime >= _flashDuration) {
        _lastFlashTime = millis();
        _flashState = 1 - _flashState;  // Toggle state
        
        if (_flashState == 0) {  // Off state
            setColor(0, 0, 0);
        } else {  // On state
            setColor(_flashColor == COLOR_RED ? 255 : 0,
                     _flashColor == COLOR_GREEN ? 255 : 0,
                     _flashColor == COLOR_BLUE ? 255 : 0);
            
            if (_flashColor == COLOR_YELLOW) {
                setColor(255, 255, 0);
            }
        }
        
        _flashCount--;
        if (_flashCount <= 0) {
            _isFlashing = false;
            setColor(0, 0, 0);
        }
    }
}