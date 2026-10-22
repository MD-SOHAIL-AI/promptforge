#pragma once
#include <Arduino.h>
#include <vector>
#ifndef MAX_INTERVALS
#define MAX_INTERVALS 10
#endif
#ifndef TOLERANCE_PERCENT
#define TOLERANCE_PERCENT 25
#endif
#ifndef PATTERN_TIMEOUT_MS
#define PATTERN_TIMEOUT_MS 2000UL
#endif
class PatternManager {
public:
    PatternManager();
    bool addKnock(unsigned long timestamp);
    bool isPatternComplete() const;
    std::vector<unsigned long> getIntervals() const;
    void clearKnocks();
    size_t getKnockCount() const;
    bool overflowed() const;
    bool matchesPattern(const std::vector<unsigned long>& storedPattern) const;
    void learnPattern(std::vector<unsigned long>& pattern);
    static std::vector<unsigned long> getPatternA();
    static std::vector<unsigned long> getPatternB();
    static std::vector<unsigned long> getPatternC();
private:
    std::vector<unsigned long> _knockTimestamps;
    bool _overflow;
};
