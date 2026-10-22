#pragma once
#include <map>
#include <string>
#include <vector>
#include <cstring>
#include <algorithm>
extern std::map<std::string, std::vector<unsigned char>> nvs;
extern bool failOpen, failWrite;
extern int openedHandles;
class Preferences {
    bool started = false, readOnly = false;
public:
    bool begin(const char*, bool ro) {
        if (started || failOpen) return false;
        started = true; readOnly = ro; ++openedHandles; return true;
    }
    void end() { if (started) --openedHandles; started = false; }
    size_t putBytes(const char* key, const void* data, size_t size) {
        if (!started || readOnly || failWrite || std::strlen(key) > 15) return 0;
        auto bytes = static_cast<const unsigned char*>(data);
        nvs[key] = std::vector<unsigned char>(bytes, bytes + size); return size;
    }
    size_t getBytesLength(const char* key) { return started && nvs.count(key) ? nvs[key].size() : 0; }
    size_t getBytes(const char* key, void* data, size_t size) {
        if (!started || !nvs.count(key) || size < nvs[key].size()) return 0;
        std::memcpy(data, nvs[key].data(), nvs[key].size()); return nvs[key].size();
    }
    bool remove(const char* key) { return started && !readOnly && !failWrite && nvs.erase(key) > 0; }
};
