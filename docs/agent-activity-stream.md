# ForgeX conversations and Agent Activity Stream

ForgeX has separate Chat and Agent destinations in a shared, conversation-first shell. Chat performs conversational model requests. Agent retains the existing Forge executor, tool policies, staged review, orchestration, and hardware approvals. The activity UI consumes structured runtime records, never inferred assistant prose.

## Runtime and transport

The existing SQLite session store now records a conversation kind and selected Chat provider/model. Agent sessions still require a project; Chat sessions may be project-independent. Existing non-null project schemas migrate transactionally, preserving old session payloads. Interrupted Chat messages recover as interrupted on restart. Session rename/delete use the same store.

`ChatService` routes through the existing model router and persists assistant message updates under a stable message ID. Existing session SSE carries `message.started`, cumulative `message.delta`, and `message.completed`, `message.failed`, or `message.cancelled`. Cumulative public text is throttled, bounded to 64,000 characters, and replay-safe. Cancellation closes the generator, handles cancellation before its first instruction, and permits retry. Concurrent turns in the same Chat session are rejected.

Provider adapters forward actual streaming output through their existing HTTP services: OpenAI, OpenRouter, Groq, Gemini, and LM Studio. Ollama uses its native streaming JSON response. No simulated token playback is used. Streaming preserves the selected provider/model, applies provider availability/local-only checks, and closes nested streams. Usage records include completion status and latency; streamed token counts remain unavailable where the adapter does not supply them.

Agent events are projected into typed, allowlisted `activity_record` snapshots inside the existing event-store transaction. Each record carries a stable public `eventType`, visual `state`, lifecycle `status`, and normalized `displayStatus` (`queued`, `running`, `success`, `warning`, or `failed`). Correlation includes run ID, agent ID, action family and tool-call ID (or summary turn/operation identity). Output and completion update the same activity. Retry attempts without a tool-call identity get a new attempt ID. Late output/start packets cannot reopen a completed identified tool call. Session SSE uses Last-Event-ID replay.

## Public activity and plans

Supported actual event families are context retrieval, public thinking summaries, plans, file reads, staged modifications/creation, search, tools, commands, generation, validation, builds, diagnostics, repairs, simulation/tests, hardware approval, flashing, serial monitoring, specialists and run termination. Context records include only bounded counts and safe summaries of verified recalled memory. Existing node events remain available to backend consumers but are omitted from the conversation timeline.

`LivePlan` maintains a stable plan ID, revision and stable step IDs. Snapshots update one checklist in place. A completed step requires successful tool evidence collected while it was active; optional build/edit/simulation verification requires the corresponding successful tool family. Duplicate steps are normalized, only one step is active, and removed unfinished steps are marked skipped. The UI shows completion counts and readable evidence labels. It does not mark pending steps successful when a run terminates.

Existing Explore/Review/Verify specialists retain their bounded, one-level policies. Each receives a unique activity identity, parent identity, assignment, streamed public summaries, tool evidence, plan and terminal status. Their work nests beneath an expandable specialist row. A Verify specialist cannot report successful verification after its latest build failed. This change does not launch additional autonomous agents or broaden delegation policy.

## Reasoning and hardware boundaries

Only explicitly public summary fields are emitted as progress. Raw reasoning/reasoning_content/thinking fields are not copied. Hidden text delimiters are filtered cumulatively, including delimiters split across chunks. Gemini thought parts and Ollama thinking fields are excluded at the provider boundary. Saved assistant messages are filtered on write/restore. This is public progress and execution evidence, not unrestricted internal reasoning.

Chat cannot execute tools or operate hardware. Optional project context uses the existing path policy plus file-size, total-size, traversal and high-confidence-secret checks.

Agent flash approvals retain the existing target/artifact binding and human approval flow. The inline card provides Review, Approve and Cancel. The manual build-panel flash action uses the existing hardware confirmation dialog. No approval or capability gate was relaxed.

## Interface and client state

The neutral dark shell provides Chat, Agent, Projects, searchable history and Settings. Markdown/GFM responses include syntax highlighting and copy actions. Chat supports retry and a reviewed draft handoff to Agent; handoffs from an existing conversation create a fresh Agent session on submission. Composers preserve drafts across navigation. Files, staged review, builds, devices, serial and terminal reuse the workspace components.

Activity rows expand for actual summaries, recalled context, plan checklists, commands, bounded output, diffs and diagnostics. Running rows use local elapsed timers; completion freezes duration and collapses the row unless explicitly expanded. Repeated reads/searches group within the same run and specialist. Conversation scrolling follows only near the bottom and offers a compact Live return control. Narrow screens use a focus-trapped details overlay/navigation drawer.

During an active Agent run, the conversation also renders a live ForgeX response assembled from the runtime's real public `assistant.summary.delta` SSE snapshots. The draft grows cumulatively per turn, is replayable after reconnect, and hands off to the persisted final assistant message when the run settles. It never simulates typing or reads provider reasoning fields.

Client reducers isolate runs/sessions, merge stable identities, reject malformed/older packets and preserve terminal messages during replay. Stale responses after project/session switching are guarded. EventSource instances, output observers and timers clean up. The initial activity window is 80 groups, messages 50, retained merged events 2,000 and each log snapshot 16,000 characters. Full orchestrated event history stays in the existing SQLite store; the UI deliberately bounds its working window.

## Main files

Backend:
- `backend/agent_runtime/{activity_stream,public_progress,chat_service,session_store,orchestration_store}.py`
- `backend/agent_runtime/{autonomous_loop,subagents,tool_runtime,tool_executor,tool_policy,tool_contracts,api_planner_provider,product_agent_service,orchestrator}.py`
- `backend/api/app.py`, `backend/api/routes/agent_runtime.py`
- `backend/model_router/router.py`, `backend/model_router/providers/{openai_provider,lmstudio,ollama}.py`
- `backend/services/llm_service.py`, `backend/runtime/{process_output,subprocess_mgr}.py`

Frontend:
- `frontend/components/agent-activity/{agent-activity-feed,agent-activity-item,activity-card,agent-status-indicator,thinking-block,context-recall-block,plan-progress,tool-call-card,command-execution-card,file-change-card,build-result-card,approval-card,chat-response-activity}.tsx`
- `frontend/components/nexus/{nexus-shell,chat-canvas,task-canvas,conversation,activity-stream}.tsx`
- `frontend/hooks/{use-chat-session,use-forge-agent-session}.ts`
- `frontend/lib/{activity-stream,chat-stream,api}.ts`, `frontend/types/index.ts`
- `frontend/app/globals.css`, `frontend/next.config.ts`

The obsolete Nexus home/composer/task-center/command-center and old agent activity component were removed after replacing their production entry points.

## Verification

- Full Python suite: 1,480 passed, four Windows symlink tests skipped. Focused activity/runtime checks cover canonical context and thinking states, replay, correlation, bounded output, and real subprocess streaming.
- UI state/render suite: 63 passed, covering stable identities, lifecycle merging, failures, approvals, frozen durations, unknown events, logs, ordering, grouping and Chat replay isolation.
- Playwright: five passing desktop/narrow tests covering destination/draft persistence, live in-place activity, nested specialists, plans, streamed Markdown, scroll preservation, subscription cleanup, durable completion, and editor canvas height. Fixtures are confined to tests.
- Electron tests: 28 passed. Release-script tests: 15 passed.
- Black/Prettier, ESLint, TypeScript and isolated Next production build run. Use `FORGEX_NEXT_DIST_DIR=.next-activity-check` for a build beside an open dev desktop; run standalone typecheck after the build so generated type files are stable.
- API/provider integration exercises the real session routes, stores, Chat service, router and OpenAI streaming parser with controlled HTTP/provider responses. Existing runtime/API tests exercise file edits, build/repair/simulation and blocked hardware approval; actual local subprocess output and observer cleanup are tested.

## Practical limits

No physical device was flashed and no paid/external live-model run was performed. Browser tests use controlled transport fixtures. Arbitrary shell execution and file delete/rename remain unavailable where the existing Forge policy/tool surface does not support them; no fake events or new permissions were added. Some hardware/workflow adapters expose lifecycle callbacks rather than continuous output. Pre-existing histories cannot reconstruct structured evidence that was never recorded. Legacy non-orchestrated runtime events remain ephemeral, consistent with their existing storage contract.

The shared decorative border tokens are transparent across workspace pages. Contrast modes derive muted text from a separate base token, avoiding CSS custom-property cycles and current-color border fallbacks. The editor workbench explicitly fills its parent height so Monaco has a visible viewport. Keyboard focus outlines and active selections remain visible.
