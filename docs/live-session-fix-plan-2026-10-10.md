# Live session fixes — plan and acceptance criteria

Scope: the 10 October session `agent-run-c4871107d7d24820962b4eb6a1f8a308`, plus the later renderer API failures. The user requested both the plan and its implementation. Existing uncommitted work and historical run records are preserved.

Status: all five implementation packages are complete. [Implementation details, passing checks and live-model qualification limits](live-session-fix-implementation-2026-10-10.md).

## 1. Allocate time without dispatching doomed model requests

- Keep the selected execution profile and the hard cumulative job deadline.
- Cap the advance reservation for verification at one third of the total task time. Actual build and review operations retain their phase limits and must fit inside the hard job deadline.
- Require meaningful generation headroom before dispatch; distinguish a verification reserve boundary from a model turn timeout and from whole-job exhaustion.
- Preserve the draft and perform the final local verification when generation reaches that boundary. Expose the specific reason in the failure report.

Acceptance: a cold, complex 900-second job no longer reserves 480 seconds up front; a request cannot be sent with only four seconds before its generation boundary. Genuine provider/turn timeouts still cancel and settle correctly.

## 2. Repair dependency configuration before continuing source generation

- Validate dependencies before waiting for all local headers to exist.
- Diagnose known Arduino-ESP32 framework facilities mistakenly listed in `lib_deps`, with exact removal/header guidance. Resolve external libraries using registry evidence; do not substitute ambiguous search results automatically.
- Pin unresolved dependency evidence into compacted context. While the failing configuration is unchanged, permit inspection and configuration repair, and reject unrelated source writes with a concrete correction.
- Revalidate immediately after a configuration repair. Bound repeated ignored repairs and preserve the failure details across saved-draft continuation.

Acceptance: replaying the observed invalid configuration discovers the blocker early; repeated module rewrites cannot bypass it; repairing the configuration permits the workflow to continue. Version and environment validation remain strict.

## 3. Preserve paired C++ interface context

- Detect obvious class-name and method-name drift between same-module headers and implementations as advisory findings.
- Include paired file paths and current declarations in repair context even when older observations are compacted.
- Keep the C++ compiler authoritative for signatures, types and successful compilation.

Acceptance: the observed `OledManager` / `OLEDManager` mismatch and renamed temperature method appear in repair evidence; consistent pairs remain clear; advisory scanning never qualifies firmware as built.

## 4. Make backend/proxy failures concrete

The available logs do not establish what terminated or replaced the backend. Do not invent a crash cause.

- Verify the configured loopback backend identity before forwarding API traffic.
- Return structured unavailable/identity-mismatch errors instead of unrelated 404/405 responses or opaque proxy 500 errors.
- Preserve methods, bodies, request IDs, authentication, queries and event streams; never replay a failed mutation.
- Record backend lifecycle and health-supervision events in the structured live log.

Acceptance: an absent or unrelated backend produces a correlated actionable error; a valid backend receives one original mutation; SSE remains streamed; lifecycle events survive loss of the launch terminal.

## 5. Verify and report

- Add regression tests for the observed failure chain, deadline distinctions, dependency repair progression, interface context and proxy transport.
- Run the affected backend, frontend state, Electron and type checks. Use isolated temporary state and owned test servers.
- Record final results and remaining live-model qualification limits here or in a linked implementation report. Do not alter the historical draft, apply generated files to the user's project, or flash hardware as part of these application fixes.
