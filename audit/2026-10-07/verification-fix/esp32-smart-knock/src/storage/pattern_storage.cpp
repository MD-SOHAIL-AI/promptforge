#include "pattern_storage.h"
#include "../pattern/pattern_manager.h"
#include <cstring>
namespace {
constexpr const char* NAMESPACE = "knock_storage";
constexpr uint32_t FORMAT_VERSION = 1;
bool validName(const char* name) {
    if (!name || !*name || std::strlen(name) > 15) return false;
    for (const char* p = name; *p; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= 'A' && *p <= 'Z') || (*p >= '0' && *p <= '9') || *p == '_')) return false;
    return true;
}
uint32_t checksum(const uint32_t* data, size_t count) {
    uint32_t hash = 2166136261UL;
    for (size_t i = 0; i < count; ++i) { hash ^= data[i]; hash *= 16777619UL; }
    return hash;
}
}
bool PatternStorage::begin() {
    // Probe and close. Each operation owns one handle; no double begin().
    const bool ok = _preferences.begin(NAMESPACE, false);
    if (ok) _preferences.end();
    return ok;
}
void PatternStorage::end() { _preferences.end(); }
bool PatternStorage::saveLearnedPattern(const std::vector<unsigned long>& pattern, const char* name) {
    if (!validName(name) || pattern.empty() || pattern.size() > MAX_INTERVALS) return false;
    uint32_t data[MAX_INTERVALS + 3] = {FORMAT_VERSION, static_cast<uint32_t>(pattern.size())};
    for (size_t i = 0; i < pattern.size(); ++i) {
        if (!pattern[i] || pattern[i] >= PATTERN_TIMEOUT_MS) return false;
        data[i + 2] = pattern[i];
    }
    const size_t count = pattern.size() + 3;
    data[count - 1] = checksum(data, count - 1);
    if (!_preferences.begin(NAMESPACE, false)) return false;
    // One bounded NVS blob prevents partial size/interval records and long keys.
    const size_t expected = count * sizeof(uint32_t);
    const bool ok = _preferences.putBytes(name, data, expected) == expected;
    _preferences.end();
    return ok;
}
bool PatternStorage::loadLearnedPattern(std::vector<unsigned long>& pattern, const char* name) {
    if (!validName(name) || !_preferences.begin(NAMESPACE, true)) return false;
    const size_t length = _preferences.getBytesLength(name);
    uint32_t data[MAX_INTERVALS + 3] = {};
    bool ok = length >= 4 * sizeof(uint32_t) && length <= sizeof(data) && length % sizeof(uint32_t) == 0;
    if (ok) ok = _preferences.getBytes(name, data, length) == length;
    _preferences.end();
    if (!ok || data[0] != FORMAT_VERSION || data[1] < 1 || data[1] > MAX_INTERVALS ||
        length != (data[1] + 3) * sizeof(uint32_t) || data[data[1] + 2] != checksum(data, data[1] + 2)) return false;
    std::vector<unsigned long> loaded;
    for (size_t i = 0; i < data[1]; ++i) {
        if (!data[i + 2] || data[i + 2] >= PATTERN_TIMEOUT_MS) return false;
        loaded.push_back(data[i + 2]);
    }
    pattern.swap(loaded);
    return true;
}
bool PatternStorage::deleteLearnedPattern(const char* name) {
    if (!validName(name) || !_preferences.begin(NAMESPACE, false)) return false;
    const bool ok = _preferences.remove(name);
    _preferences.end();
    return ok;
}
bool PatternStorage::patternExists(const char* name) { std::vector<unsigned long> pattern; return loadLearnedPattern(pattern, name); }
