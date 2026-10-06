# Live tool-history crash: reproduced and fixed

## Incident

The live desktop run `agent-run-4a02ea021db24fa2b4b7b8492ee4753c` used
OpenRouter model `nvidia/nemotron-3-ultra-550b-a55b:free` on 5 October 2026.
The backend process was PID 27876; its captured source fingerprint matches
this checkout's Python sources.

| Time, IST | Actual logged event |
| --- | --- |
| 19:28:28 | First provider request started. |
| 19:28:35 | First response completed with HTTP 200 after 6,891 ms. |
| 19:28:35 | `list_files` executed successfully; duration 139 ms. |
| 19:28:36 | Second model request started with the previous assistant/tool history. |
| 19:28:37 | `PROVIDER_REQUEST_FAILED`: `Cannot read properties of undefined (reading 'name')`. |
| 19:28:38 | Agent run marked failed. |

Evidence: [original backend log](../../../.forgex/logs/live-runtime.backend-df0bd26df75345d482597a9e25240c39.jsonl),
especially lines 71, 72 and 77. The three error records describe one failure.

## Proven cause

[RouterProductPlanner](../../../backend/agent_runtime/router_planner.py), around
lines 866–887, commits assistant calls in ForgeX's canonical form:

```json
{"id":"call-list","name":"list","arguments":{"path":"."}}
```

On the next turn, `_generate` forwards that history in `forgex_messages`.
[OpenCodeProvider._payload](../../../backend/model_router/providers/opencode.py)
passes it unchanged into the Node worker. That is the intended boundary contract.

Both `nativeMessages` and `executeSDK` in
[the model engine](../../../model-router/src/engine.mjs) instead accessed
`call.function.name` and parsed `call.function.arguments`. They expected an
OpenAI wire envelope:

```json
{"id":"call-list","function":{"name":"list","arguments":"{\"path\":\".\"}"}}
```

The canonical call has no `function` property. Accessing its `name` threw before
the provider HTTP request could be sent. The first turn had no assistant tool
history, which explains why it succeeded.

Two local pre-fix tests reproduced the **exact same TypeError**, one through the
OpenRouter/native engine and another through the SDK path. Their stacks point
to the two conversions above: [before-fix evidence](before-fix.log).

The earlier Node history test supplied a nested OpenAI envelope. Planner tests
used fake adapters, while real-worker tests covered first requests. Those
fixtures did not exercise the actual planner's canonical history crossing the
worker boundary. The new regression closes that gap.

## Fix

One `historyToolCalls` converter now serves both execution paths:

- Accept canonical `{id, name, arguments}` calls and legacy nested `function` envelopes.
- Accept object arguments and JSON-encoded object arguments; preserve opaque IDs and tool names.
- Keep the Python planner's canonical persistence format.
- Reject missing IDs/names, malformed collections, invalid JSON and non-object arguments before HTTP dispatch.
- Return a structured `MODEL_MESSAGE_HISTORY_INVALID`, category `invalid_request`, with `retryable: false` for invalid history. The message contains no raw arguments.
- Use `Map` for ID/name correlation so IDs cannot collide with object prototype properties.
- Preserve the caller's input history during conversion.

Production source changed: `model-router/src/engine.mjs`. Two regression files
were added. `worker.cjs` and `engine.cjs` were rebuilt through the project build.
Provider selection, tool policy and workspace permissions retain their existing
owners.

## Verification

| Check | Result | Evidence |
| --- | --- | --- |
| Pre-fix exact crash | Both reproduced failures | [log](before-fix.log) |
| Affected Python groups | **252 passed**, command exit 0 | [log](python-verified.log), [JUnit](python-verified.xml) |
| Node: router, Harness, Electron and release scripts | **133 passed**, command exit 0 | [log](node-all.log) |
| Model-router, Harness and Electron build | Passed, command exit 0 | [log](desktop-build.log) |
| Source release-content scan | Passed, command exit 0 | [log](source-release-scan.log) |
| Python outcomes including prior remediation groups | **2,262 unique passed, 4 skipped**; overlapping reruns counted once | [combined ledger](combined-python-outcomes.json) |

[Python integration regressions](../../../tests/unit/test_live_tool_history.py)
run the real planner, router, bundled Node worker, local HTTP endpoint and real
workspace tool executor. Both OpenRouter and Mistral complete **five consecutive
model turns**, including two tool calls in one batch and five real read/list
executions. Each later request must contain every earlier call ID, name,
normalized argument object and tool result. Test project bytes remain unchanged.

Two additional worker tests verify that invalid history reaches Python as a
terminal `invalid_request` error, with **zero HTTP requests and no retry**.

[Node regressions](../../../model-router/tests/tool-history.test.mjs) verify
canonical and legacy history, object and JSON arguments, non-mutating conversion,
opaque IDs, malformed history, OpenAI Responses, Anthropic Messages, Gemini,
and both AI SDK 6 and AI SDK 7 execution paths.

All provider responses in the new tests come from local HTTP fixtures using
synthetic credentials. This establishes the serialization fix; it does not
establish external provider availability or qualify every catalog model.
One early integration assertion compared the provider's empty list arguments
with the planner's approved arguments; the planner legitimately inserts
`path: "."`. The assertion now compares transmitted arguments with the actual
executed arguments. All five turns had already succeeded in that earlier run.

## Running application

The Python provider starts a fresh Node worker for each physical model attempt,
loading the rebuilt `dist/model-router/worker.cjs`. Subsequent requests from this
source checkout therefore use the corrected conversion. The already failed run
was preserved; it was not automatically replayed against the user's workspace.
No external account request or firmware operation was initiated for this fix.
