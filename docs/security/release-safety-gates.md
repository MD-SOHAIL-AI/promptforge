# Release Safety Gates

A compatibility cutover is releasable only when backend.release.cutover_audit reports every gate passing.

- Provider descriptors declare no apply, build, flash, monitor, approve, secret-access, shell-execution, or active-workspace mutation authority.
- Apply and hardware mutations require unexpired ForgeX approvals bound to the exact review/artifact and, for flash, device, port, board, and command hashes.
- Every durable run binds one immutable Agent Profile version.
- Every persisted model-call usage record has an explainable routing decision.
- Connections contain no raw credentials. API keys remain in the OS credential store; CLI sessions remain CLI-owned.
- Codex and AGY participate through Connection and AgentAdapter contracts.
- Diagnostic exports exclude credentials, auth output, environment secrets, prompts, responses, source, diffs, and raw logs.
- Append-only workflow events and terminal-event uniqueness remain enforced by SQLite.

A passing static scan is insufficient: containment, malicious-path, cancellation, timeout, replay, approval-expiry, wrong-device, process-tree cleanup, corruption recovery, and full workflow parity tests must also pass.
