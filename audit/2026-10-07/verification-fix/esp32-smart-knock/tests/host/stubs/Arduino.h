#pragma once
#include <cstdint>
#include <cstddef>
#include <string>
#include <sstream>
constexpr int INPUT = 0, OUTPUT = 1, INPUT_PULLUP = 2, LOW = 0, HIGH = 1;
extern unsigned long fakeNow;
extern int fakeAdc, fakeButton, adcReads, pinModes[40], pinValues[40], buzzerRises;
inline unsigned long millis() { return fakeNow; }
inline int analogRead(int) { ++adcReads; return fakeAdc; }
inline int digitalRead(int) { return fakeButton; }
inline void pinMode(int pin, int mode) { pinModes[pin] = mode; }
inline void digitalWrite(int pin, int value) {
    if (pin == 14 && value == HIGH && pinValues[pin] == LOW) ++buzzerRises;
    pinValues[pin] = value;
}
inline void analogWrite(int pin, int value) { pinValues[pin] = value; }
using String = std::string;
struct TestSerial {
    std::string text;
    void begin(int) {}
    template<typename T> void print(const T& value) { std::ostringstream s; s << value; text += s.str(); }
    template<typename T> void println(const T& value) { print(value); text += '\n'; }
    void println() { text += '\n'; }
};
extern TestSerial Serial;
