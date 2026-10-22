#include <Arduino.h>
#include "knock/knock_detector.h"
#include "pattern/pattern_manager.h"
#include "output/led_controller.h"
#include "output/buzzer.h"
#include "storage/pattern_storage.h"
constexpr int PIEZO_PIN = 34, LED_RED_PIN = 25, LED_GREEN_PIN = 26, LED_BLUE_PIN = 27;
constexpr int BUZZER_PIN = 14, BUTTON_PIN = 0;
constexpr int KNOCK_THRESHOLD = 500, DEBOUNCE_DELAY = 30;
constexpr unsigned long BUTTON_DEBOUNCE_MS = 30, LONG_PRESS_TIME = 1000;
constexpr unsigned long LEARN_PATTERN_TIMEOUT = 5000, FEEDBACK_HOLD_MS = 1200;
KnockDetector knockDetector(PIEZO_PIN, KNOCK_THRESHOLD, DEBOUNCE_DELAY);
PatternManager patternManager;
LedController ledController(LED_RED_PIN, LED_GREEN_PIN, LED_BLUE_PIN);
Buzzer buzzer(BUZZER_PIN);
PatternStorage patternStorage;
enum SystemState { STATE_IDLE, STATE_KNOCK_DETECTED, STATE_WAITING_FOR_NEXT_KNOCK,
    STATE_PATTERN_COMPLETE, STATE_ANALYZE_PATTERN, STATE_SUCCESS, STATE_UNKNOWN, STATE_LEARNING_MODE };
SystemState currentState = STATE_IDLE;
unsigned long buttonPressStartTime = 0, buttonChangedAt = 0, learningStartedAt = 0, feedbackStartedAt = 0;
bool buttonRawPressed = false, buttonIsPressed = false, longPressHandled = false;
std::vector<unsigned long> learnedPattern;
bool haveLearnedPattern = false;
void returnToIdle() {
    patternManager.clearKnocks(); buzzer.off();
    ledController.setColor(LedController::COLOR_BLUE); currentState = STATE_IDLE;
    Serial.println("State: IDLE - listening");
}
void enterLearningMode() {
    patternManager.clearKnocks(); buzzer.off(); knockDetector.reset();
    learningStartedAt = millis(); currentState = STATE_LEARNING_MODE;
    ledController.setColor(LedController::COLOR_YELLOW);
    Serial.println("Learning mode: perform 2-11 knocks within 5 seconds");
}
void handleButton(unsigned long now) {
    const bool pressed = digitalRead(BUTTON_PIN) == LOW;
    if (pressed != buttonRawPressed) { buttonRawPressed = pressed; buttonChangedAt = now; }
    if (now - buttonChangedAt >= BUTTON_DEBOUNCE_MS && buttonIsPressed != buttonRawPressed) {
        buttonIsPressed = buttonRawPressed;
        if (buttonIsPressed) { buttonPressStartTime = now; longPressHandled = false; }
        else if (!longPressHandled) { Serial.println("Short press: clear sequence"); returnToIdle(); }
    }
    if (buttonIsPressed && buttonRawPressed && !longPressHandled && now - buttonPressStartTime >= LONG_PRESS_TIME) {
        longPressHandled = true; enterLearningMode();
    }
}
void recordKnock(unsigned long timestamp) {
    if (!patternManager.addKnock(timestamp)) { Serial.println("Sequence too long"); return; }
    Serial.print("Knock #"); Serial.println(patternManager.getKnockCount());
    const auto intervals = patternManager.getIntervals();
    if (!intervals.empty()) { Serial.print("Interval: "); Serial.print(intervals.back()); Serial.println(" ms"); }
}
void analyzePattern(unsigned long now) {
    Serial.println("Pattern complete; comparing pattern...");
    int color = LedController::COLOR_GREEN;
    if (patternManager.matchesPattern(PatternManager::getPatternA())) { Serial.println("Pattern A recognized"); buzzer.confirmationBeep(); }
    else if (patternManager.matchesPattern(PatternManager::getPatternB())) { Serial.println("Pattern B recognized"); buzzer.confirmationBeep(); }
    else if (patternManager.matchesPattern(PatternManager::getPatternC())) { Serial.println("Pattern C recognized"); color = LedController::COLOR_BLUE; buzzer.doubleBeep(100); }
    else if (haveLearnedPattern && patternManager.matchesPattern(learnedPattern)) { Serial.println("Learned pattern recognized"); buzzer.confirmationBeep(); }
    else { Serial.println("Unknown pattern"); color = LedController::COLOR_RED; buzzer.errorBeep(); }
    ledController.setColor(color); feedbackStartedAt = now;
    currentState = color == LedController::COLOR_RED ? STATE_UNKNOWN : STATE_SUCCESS;
}
void finishLearning(unsigned long now) {
    const auto candidate = patternManager.getIntervals();
    if (!patternManager.overflowed() && !candidate.empty() && patternStorage.saveLearnedPattern(candidate, "learned")) {
        learnedPattern = candidate; haveLearnedPattern = true;
        Serial.println("Learned pattern saved to NVS");
        ledController.flashGreen(5, 100); buzzer.confirmationBeep(); currentState = STATE_SUCCESS;
    } else {
        Serial.println("Learning failed: insufficient knocks, overflow, or NVS write failure");
        ledController.flashRed(3, 150); buzzer.errorBeep(); currentState = STATE_UNKNOWN;
    }
    patternManager.clearKnocks(); feedbackStartedAt = now;
}
void setup() {
    Serial.begin(115200); pinMode(BUTTON_PIN, INPUT_PULLUP);
    knockDetector.begin(); ledController.begin(); buzzer.begin();
    buttonRawPressed = digitalRead(BUTTON_PIN) == LOW; buttonChangedAt = millis();
    if (patternStorage.begin()) haveLearnedPattern = patternStorage.loadLearnedPattern(learnedPattern, "learned");
    else Serial.println("NVS unavailable; learning saves may fail");
    Serial.println(haveLearnedPattern ? "System started; learned pattern loaded" : "System started; no learned pattern");
    returnToIdle();
}
void loop() {
    const unsigned long now = millis();
    handleButton(now);
    // Sample once; dispatch this event without consuming it a second time.
    const bool knock = knockDetector.update();
    ledController.update(); buzzer.update();
    switch (currentState) {
        case STATE_IDLE:
            if (knock) { recordKnock(knockDetector.getLastKnockTime()); currentState = STATE_KNOCK_DETECTED; }
            break;
        case STATE_KNOCK_DETECTED:
        case STATE_WAITING_FOR_NEXT_KNOCK:
            if (patternManager.isPatternComplete()) {
                // An event after the deadline starts a new sequence.
                if (knock) { analyzePattern(now); patternManager.clearKnocks(); recordKnock(knockDetector.getLastKnockTime()); currentState = STATE_KNOCK_DETECTED; }
                else currentState = STATE_PATTERN_COMPLETE;
            } else {
                if (knock) recordKnock(knockDetector.getLastKnockTime());
                currentState = STATE_WAITING_FOR_NEXT_KNOCK;
            }
            break;
        case STATE_PATTERN_COMPLETE:
        case STATE_ANALYZE_PATTERN:
            analyzePattern(now);
            if (knock) { patternManager.clearKnocks(); recordKnock(knockDetector.getLastKnockTime()); currentState = STATE_KNOCK_DETECTED; }
            break;
        case STATE_SUCCESS:
        case STATE_UNKNOWN:
            if (knock) { returnToIdle(); recordKnock(knockDetector.getLastKnockTime()); currentState = STATE_KNOCK_DETECTED; }
            else if (now - feedbackStartedAt >= FEEDBACK_HOLD_MS) returnToIdle();
            break;
        case STATE_LEARNING_MODE:
            if (now - learningStartedAt >= LEARN_PATTERN_TIMEOUT || patternManager.isPatternComplete()) finishLearning(now);
            else if (knock) recordKnock(knockDetector.getLastKnockTime());
            break;
    }
}
