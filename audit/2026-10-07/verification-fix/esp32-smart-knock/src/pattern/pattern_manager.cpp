#include "pattern_manager.h"
PatternManager::PatternManager() : _overflow(false) { _knockTimestamps.reserve(MAX_INTERVALS + 1); }
bool PatternManager::addKnock(unsigned long timestamp) {
    if (_knockTimestamps.size() >= MAX_INTERVALS + 1) { _overflow = true; return false; }
    _knockTimestamps.push_back(timestamp);
    return true;
}
bool PatternManager::isPatternComplete() const {
    // A single knock must time out too, rather than stranding the waiting state.
    return _overflow || (!_knockTimestamps.empty() && millis() - _knockTimestamps.back() >= PATTERN_TIMEOUT_MS);
}
std::vector<unsigned long> PatternManager::getIntervals() const {
    std::vector<unsigned long> result;
    for (size_t i = 1; i < _knockTimestamps.size(); ++i)
        result.push_back(_knockTimestamps[i] - _knockTimestamps[i - 1]);
    return result;
}
void PatternManager::clearKnocks() { _knockTimestamps.clear(); _overflow = false; }
size_t PatternManager::getKnockCount() const { return _knockTimestamps.size(); }
bool PatternManager::overflowed() const { return _overflow; }
bool PatternManager::matchesPattern(const std::vector<unsigned long>& stored) const {
    const auto intervals = getIntervals();
    if (_overflow || stored.empty() || stored.size() != intervals.size()) return false;
    for (size_t i = 0; i < intervals.size(); ++i) {
        if (!stored[i]) return false;
        const uint64_t difference = intervals[i] > stored[i] ? intervals[i] - stored[i] : stored[i] - intervals[i];
        if (difference * 100 > uint64_t(stored[i]) * TOLERANCE_PERCENT) return false;
    }
    return true;
}
void PatternManager::learnPattern(std::vector<unsigned long>& pattern) { pattern = _overflow ? std::vector<unsigned long>{} : getIntervals(); }
std::vector<unsigned long> PatternManager::getPatternA() { return {500}; }
std::vector<unsigned long> PatternManager::getPatternB() { return {400, 400}; }
std::vector<unsigned long> PatternManager::getPatternC() { return {800, 300}; }
