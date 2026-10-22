#include "stubs/Arduino.h"
#include "stubs/Preferences.h"
#include <cassert>
#include <iostream>
#include <limits>
unsigned long fakeNow = 0;
int fakeAdc = 0, fakeButton = HIGH, adcReads = 0, pinModes[40] = {}, pinValues[40] = {}, buzzerRises = 0;
TestSerial Serial;
std::map<std::string, std::vector<unsigned char>> nvs;
bool failOpen = false, failWrite = false;
int openedHandles = 0;
// Compile and execute the real application loop, with only hardware APIs stubbed.
#include "../../src/main.cpp"

void tick(unsigned long now, int adc = 0, int button = HIGH) {
    fakeNow = now; fakeAdc = adc; fakeButton = button;
    const int before = adcReads; loop(); assert(adcReads == before + 1);
}
void resetApplication(unsigned long now = 0) {
    fakeNow = now; fakeAdc = 0; fakeButton = HIGH;
    buttonRawPressed = buttonIsPressed = longPressHandled = false;
    haveLearnedPattern = false; learnedPattern.clear();
    Serial.text.clear(); setup(); buzzerRises = 0;
}
void knockAt(unsigned long now) {
    tick(now - 40); tick(now - 1); tick(now, 1000); tick(now + 1); tick(now + 31);
}
int main() {
    static_assert(sizeof(unsigned long) == 4, "Match ESP32 32-bit millis arithmetic");
    KnockDetector detector(34, 500, 30);
    fakeNow = 0; detector.begin(); fakeAdc = 500; assert(!detector.update());
    fakeNow = 1; fakeAdc = 501; assert(detector.update());
    fakeNow = 2; assert(!detector.update());
    fakeNow = 5; fakeAdc = 0; assert(!detector.update());
    fakeNow = 20; fakeAdc = 900; assert(!detector.update());
    fakeNow = 21; fakeAdc = 0; assert(!detector.update());
    fakeNow = 51; assert(!detector.update());
    fakeNow = 52; fakeAdc = 900; assert(detector.update());
    detector.setThreshold(1000); fakeNow = 53; assert(!detector.update());
    std::cout << "PASS: pulse detection, threshold, ringing suppression and rearm\n";

    PatternManager patterns;
    patterns.addKnock(1500); patterns.addKnock(1900); patterns.addKnock(2700);
    assert(patterns.getIntervals() == (std::vector<unsigned long>{400, 800}));
    patterns.clearKnocks(); patterns.addKnock(100); patterns.addKnock(475);
    assert(patterns.matchesPattern({500}));
    patterns.clearKnocks(); patterns.addKnock(100); patterns.addKnock(725);
    assert(patterns.matchesPattern({500}));
    patterns.clearKnocks(); patterns.addKnock(100); patterns.addKnock(726);
    assert(!patterns.matchesPattern({500}));
    patterns.clearKnocks(); patterns.addKnock(100); fakeNow = 2100;
    assert(patterns.isPatternComplete() && !patterns.matchesPattern({}));
    patterns.clearKnocks();
    const auto nearWrap = std::numeric_limits<unsigned long>::max() - 100;
    patterns.addKnock(nearWrap); patterns.addKnock(399);
    assert(patterns.getIntervals()[0] == 500);
    fakeNow = 2399; assert(patterns.isPatternComplete());
    patterns.clearKnocks();
    for (int i = 0; i < 50; ++i) patterns.addKnock(i * 100);
    assert(patterns.getKnockCount() == MAX_INTERVALS + 1 && patterns.overflowed());
    assert(!patterns.matchesPattern(std::vector<unsigned long>(MAX_INTERVALS, 100)));
    assert(PatternManager::getPatternC().size() == 2);
    std::cout << "PASS: intervals, tolerance boundaries, single-knock timeout, rollover and overflow\n";

    PatternStorage storage;
    assert(storage.begin() && openedHandles == 0);
    assert(storage.saveLearnedPattern({500, 300}, "learned") && openedHandles == 0);
    PatternStorage restarted;
    std::vector<unsigned long> loaded;
    assert(restarted.loadLearnedPattern(loaded, "learned") && loaded == (std::vector<unsigned long>{500, 300}));
    failWrite = true; assert(!storage.saveLearnedPattern({800}, "learned")); failWrite = false;
    assert(storage.loadLearnedPattern(loaded, "learned") && loaded.size() == 2);
    failOpen = true; assert(!storage.begin() && !storage.loadLearnedPattern(loaded, "learned")); failOpen = false;
    assert(storage.begin() && openedHandles == 0);
    assert(!storage.saveLearnedPattern({500}, "abcdefghijklmnop"));
    assert(!storage.saveLearnedPattern({}, "learned") && !storage.saveLearnedPattern({0}, "learned"));
    assert(!storage.saveLearnedPattern(std::vector<unsigned long>(MAX_INTERVALS + 1, 100), "learned"));
    nvs["learned"][8] ^= 1;
    const auto before = loaded; assert(!storage.loadLearnedPattern(loaded, "learned") && loaded == before);
    nvs["learned"] = std::vector<unsigned char>(10000, 1);
    assert(!storage.loadLearnedPattern(loaded, "learned") && !storage.patternExists("learned"));
    assert(storage.deleteLearnedPattern("learned") && openedHandles == 0);
    std::cout << "PASS: NVS restart roundtrip, write/open failures, key limits, bounded corrupt reads\n";

    nvs.clear(); resetApplication();
    assert(pinModes[0] == INPUT_PULLUP && pinModes[34] == INPUT);
    knockAt(100); knockAt(600); tick(2600); tick(2601);
    assert(currentState == STATE_SUCCESS && pinValues[26] == 255 && buzzerRises == 1);
    tick(2701); assert(pinValues[14] == LOW);
    knockAt(2800); assert(patternManager.getKnockCount() == 1 && currentState == STATE_WAITING_FOR_NEXT_KNOCK);
    std::cout << "PASS: once-per-loop event dispatch, Pattern A short beep, input during feedback\n";

    resetApplication(); knockAt(100); knockAt(900); knockAt(1200); tick(3200); tick(3201);
    assert(currentState == STATE_SUCCESS && pinValues[27] == 255);
    tick(3301); tick(3401); tick(3501); assert(buzzerRises == 2 && pinValues[14] == LOW);
    std::cout << "PASS: three-knock Pattern C, blue feedback, two separated nonblocking beeps\n";

    resetApplication(); knockAt(100); tick(2100); tick(2101);
    assert(currentState == STATE_UNKNOWN && pinValues[25] == 255);
    tick(3301); assert(currentState == STATE_IDLE);
    std::cout << "PASS: unknown and single-knock sequences return to listening\n";

    resetApplication(); knockAt(100);
    tick(200, 0, LOW); tick(210, 0, HIGH); tick(250);
    assert(patternManager.getKnockCount() == 1); // Bounce cannot clear a sequence.
    tick(300, 0, LOW); tick(330, 0, LOW); tick(400, 0, HIGH); tick(430);
    assert(currentState == STATE_IDLE && patternManager.getKnockCount() == 0);
    tick(500, 0, LOW); tick(530, 0, LOW); tick(1530, 0, LOW);
    assert(currentState == STATE_LEARNING_MODE && pinValues[25] == 255 && pinValues[26] == 255);
    tick(1600, 0, LOW); assert(learningStartedAt == 1530);
    tick(1700); tick(1730); assert(currentState == STATE_LEARNING_MODE);
    tick(6530); assert(currentState == STATE_UNKNOWN && !haveLearnedPattern);
    std::cout << "PASS: button debounce, short clear, long press once, empty learning timeout\n";

    resetApplication(); fakeNow = 100; enterLearningMode(); knockAt(200); knockAt(900); tick(2900);
    assert(haveLearnedPattern && learnedPattern == (std::vector<unsigned long>{700}) && currentState == STATE_SUCCESS);
    resetApplication(); assert(haveLearnedPattern && learnedPattern[0] == 700);
    fakeNow = 100; enterLearningMode(); knockAt(200); knockAt(1000);
    failWrite = true; tick(3000); failWrite = false;
    assert(currentState == STATE_UNKNOWN && learnedPattern[0] == 700);
    std::cout << "PASS: learning commits only successful NVS saves and reloads after restart\n";

    resetApplication(nearWrap); enterLearningMode(); tick(4898);
    assert(currentState == STATE_LEARNING_MODE); tick(4899); assert(currentState == STATE_UNKNOWN);
    assert(openedHandles == 0);
    std::cout << "PASS: learning deadline across millis rollover\nALL FIRMWARE BEHAVIOR CHECKS PASSED\n";
}
