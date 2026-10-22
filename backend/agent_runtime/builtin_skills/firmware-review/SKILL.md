# Firmware change review

Review embedded changes for correctness and hardware safety.

Check board/framework compatibility, GPIO constraints, initialization ordering, blocking loops, watchdog risk, memory use, library compatibility, error handling, and accidental credential inclusion. Prefer minimal diffs. A successful compiler build proves compilation, not hardware correctness; distinguish those claims clearly.
