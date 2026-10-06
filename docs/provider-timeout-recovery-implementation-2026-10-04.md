# Provider timeout and recovery implementation

Implemented the [review plan](provider-timeout-permanent-fix-plan-2026-10-04.md)
on 4 October 2026 using the existing router, durable jobs and sealed checkpoints.

## Resulting behavior

- OpenAI-compatible `[DONE]` and Responses API terminal markers close the
  response immediately. Finish reasons, truncation and usage survive settlement.
- Agent requests have separate connect, first-event, idle and total deadlines.
  Productive streaming can continue beyond NVIDIA's former 120-second total
  limit. Heartbeats do not extend the semantic progress deadline.
- Short transient failures, including HTTP 503, retain bounded request retries.
  Long silent or stalled responses defer to persisted job recovery. Cancellation
  releases the HTTP response. Turn limits and exhausted job time have separate
  classifications, so a turn limit does not claim that the job budget ran out.
- Physical attempts record safe identifiers, response headers/status, first and
  last event timing, event count, completion and timeout stage. Progress updates
  contain metadata and are throttled; partial tool arguments stay private until
  a complete attempt is validated. These diagnostics survive restart.
- The recovery API exposes separate permissions for inspecting saved work,
  continuing generation and verifying locally. Local verification checks project
  trust, local tool/time allowance and the presence of firmware source. Exhausted
  model allowance can block generation while allowing a local build.
- Trust changes refresh controls immediately and preserve existing budgets,
  recovery counts and outage history. A configuration-only draft directs the
  user to continue generation before building.
- The renderer observes pending durable jobs after a run settles, follows their
  replacement runs and reconnects to normal run events. Polling backs off on
  errors and hidden windows; stale replies cannot replace a newer selected task.
- Task contracts persist deliverables inferred from active requirements and
  acceptance checks. Checkpoints project current file presence and verified
  evidence separately. Missing source takes precedence over a premature build
  milestone. Planner instructions favor one or two complete edits per turn.

## Default agent transport policy

| Boundary | Default |
| --- | --- |
| Connection | 10 seconds |
| First meaningful event | 120 seconds, or a shorter configured provider timeout |
| Idle after meaningful output | 60 seconds |
| Physical attempt total | 300 seconds |
| Request write | 30 seconds |
| Connection pool wait | 5 seconds |

Host execution contexts can supply a validated policy. Parent turn/phase
deadlines cap these limits. Policies and execution callbacks are never sent in
model payloads. Retry admission reserves a useful first-response window before
the parent deadline. Productive attempt/turn caps do not count as endpoint
outages in the circuit breaker.

## Verification

Regression coverage exercises held-open completed streams, terminal usage,
productive generation beyond the old total limit, heartbeat-only responses,
idle stalls, cancellation, turn versus job deadlines, retry charging, circuit
classification, trust changes, incomplete drafts, independent local/model
budgets and persisted diagnostics. Browser coverage checks permission controls,
durable trust blocks and following replacement runs without task submissions.

Commands used:

| Check | Result |
| --- | --- |
| Full backend suite | 2,200 passed; 4 Windows symlink checks skipped |
| UI state suite | 77 passed |
| Frontend type checking and lint | Passed |
| New recovery browser scenarios | 4 passed |
| Broad browser suite | 47 passed; the remaining chat case passed after its test setup was corrected and both affected chat cases were rechecked |

The browser correction explicitly selects Chat before creating a conversation;
it does not change the application's chat behavior. Across the broad run and
focused recheck, all 48 browser scenarios were verified.

```powershell
.venv\Scripts\python.exe -m pytest -q
npm.cmd run test:ui-state
npm.cmd run typecheck:frontend
npm.cmd run lint:frontend
```

Playwright runs all tests from `frontend/playwright.config.ts` against an isolated
frontend at port 3014. Test transports and temporary workspaces avoid dispatching
the user's saved task or invoking a real model endpoint.

Real NVIDIA qualification remains pending: the review identified a timeout,
without evidence proving which upstream stream condition triggered that live
incident. External provider availability remains outside the desktop's control.

## Loading the changes

Restart the desktop application to load the updated backend and renderer.
For the saved configuration-only project, enable project trust for local builds
and tests, then choose **Retry selected model** to continue generating firmware.
**Verify locally** becomes available when the draft has firmware source and the
local checks permit it. Settled work and cumulative budgets remain attached to
the same durable job.
