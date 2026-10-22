# Provider-outage recovery plan

The September 30 live incident used the selected NVIDIA NIM Nemotron endpoint. At turn 20, it returned HTTP 503, timed out on subsequent attempts, and the replacement run reached the 15-minute job deadline before building. The generated files remained an unverified recovery draft. This is an upstream availability failure combined with a ForgeX time-allocation failure; changing ForgeX cannot make an unavailable endpoint answer.

## Recovery invariant

Every selected provider and every agent runtime must use the same bounded request policy. A transient provider failure may pause a job, but it must not replay a tool effect, silently change providers, claim a build, or spend the last usable part of the runtime budget on retries. A saved draft must remain tied to its checkpoint and cumulative budget.

## Implemented policy

1. A generation turn can use at most one third of its remaining phase time, capped by its configured turn limit. Generation reserves 120–300 seconds for a required build and 60 seconds for requirement review; the parent deadline leaves five seconds for persistence. Reviewers can use the reserved review time.
2. The shared retry layer declines a new physical attempt when its backoff plus 15 seconds would overrun the turn deadline. The original provider error is surfaced to durable recovery.
3. The circuit settles every physical request and opens after two transient failures or a positive server retry hint. Endpoint capacity and one cooldown probe are shared across processes. Transient unavailability and timeouts use durable 30-, 60-, then 120-second cooldowns within three automatic cycles and a ten-minute outage episode that survives restart. A provider's longer `Retry-After` remains authoritative. Exhausted recovery stays paused for explicit continuation.
4. When less than the job's recovery reserve remains, ForgeX pauses for a time-budget extension instead of automatically retrying. A direct resume cannot bypass that blocker. The saved draft and provider/model selection remain unchanged.
5. The continuous agent loop and staged tool runtime use the shared async engine and cumulative job deadline. Active work and short request waits count toward runtime; durable outage cooldowns are accounted separately. Intake reservations follow clarification into the saved job.

The current architecture, migration, rollback flag, and new verification boundaries are documented in [agent-outage-redesign-implementation.md](agent-outage-redesign-implementation.md). The test counts below describe the earlier implementation; the linked document contains current results.

## Acceptance checks

- Unit and integration tests cover 503, timeout, the turn deadline, physical retry suppression, durable cooldown, restart recovery, low-time pause, and both runtime paths. The focused run passed 206 tests with one Windows symlink skip.
- The full Python suite passed 2,075 tests with four Windows symlink skips. Frontend type checking, lint, and 71 UI-state tests passed after the final edit.
- A live selected-model canary should show either successful progress to build/verification or a clear paused state with an intact draft and remaining runtime. It cannot certify endpoint uptime. Repeated 503s or timeouts must be reported as provider unavailability.
- No provider fallback is automatic. A user may explicitly choose another configured model/provider if the selected endpoint remains unavailable.

The remaining external work is a real-provider qualification campaign across the supported prompts and hardware sign-off. These are evidence gates; no local retry policy can guarantee an external model's availability or firmware behavior.
