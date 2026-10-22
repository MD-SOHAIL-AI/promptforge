#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include <vector>
class PatternStorage {
public:
    bool begin();
    void end();
    bool saveLearnedPattern(const std::vector<unsigned long>& pattern, const char* patternName);
    bool loadLearnedPattern(std::vector<unsigned long>& pattern, const char* patternName);
    bool deleteLearnedPattern(const char* patternName);
    bool patternExists(const char* patternName);
private:
    Preferences _preferences;
};
