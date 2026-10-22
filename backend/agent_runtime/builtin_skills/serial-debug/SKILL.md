# Embedded serial debugging

Use serial output as runtime evidence, not as a reason to rewrite unrelated code.

- Confirm the selected port and expected baud rate from project context when available.
- Look for reset reasons, watchdog messages, panic/backtrace output, sensor initialization failures, and repeated boot loops.
- Correlate messages with the relevant source before editing.
- Serial monitoring is observation-only unless explicit input is required.
- Firmware flashing remains a separately approved hardware action.
