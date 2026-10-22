# ForgeX Harness Host

## Outcome

Python owns agent execution, durable jobs, budgets, workspace changes, builds,
and physical-hardware operations. Desktop and CLI clients use this same engine.
The TypeScript harness is an optional shadow host for diagnostics and event
mirroring. It starts only when `FORGEX_ENABLE_SHADOW_HARNESS=1`; a failed shadow
startup cannot prevent the Python engine from starting.

The harness host is not an arbitrary shell wrapper. Its boundaries are explicit:

- loopback-only HTTP and a per-launch 256-bit bearer token;
- versioned JSON-RPC envelopes and strict JSON values;
- append-only, fsynced, checksum-chained session logs;
- durable idempotency records with request-fingerprint conflict detection;
- one-settlement tool results, monotonic policy guards, and hard deadlines;
- bounded subagent depth, concurrency, duration, and durable parent/child events;
- bounded parallel and pipeline workflows;
- manifest-validated plugins that stay disabled until capabilities are trusted;
- a terminal seam that fails closed unless full sandbox and device isolation are verified;
- no serial-port or hardware mutation commands through the generic terminal.

## Process topology

```text
Electron (process owner)
  |-- Next.js renderer
  |-- Optional TypeScript shadow host (127.0.0.1, launch token)
  `-- FastAPI Agent Engine (127.0.0.1, desktop token)
          |-- durable jobs, budgets, checkpoints and recovery
          |-- model-provider adapters
          |-- staged workspace / ChangeSets
          |-- PlatformIO build and sealed upload manifests
          `-- typed hardware services + one-time exact approvals
```

When shadow mode is enabled, Electron injects the host's loopback URL and launch
token into Python. FastAPI exposes authenticated status and provider-catalog
diagnostics and mirrors session creation, user messages, and run starts. Mirror
failures are observable and cannot break the authoritative run. The CLI starts
or attaches to FastAPI without requiring Electron or the shadow host.

## Provider architecture

Built-in adapters cover DeepSeek, Anthropic, OpenAI, OpenRouter, Groq, NVIDIA
NIM, Gemini, Ollama, and LM Studio. A protocol adapter supports user-defined:

- `openai-completions`
- `openai-responses`
- `anthropic-messages`

Definitions and non-secret settings use atomic persistence. API keys use the
existing OS credential store. Install and removal update the live router, and
removal fails if a primary or fallback route still references the provider.

## Hardware authority

All physical mutations remain typed host operations. Agent flash bindings
require a sealed manifest for the complete upload set, including image hashes
and offsets. Confirmation uploads those exact bytes without rebuilding. Every
mutation binding includes the run,
project, stable device identity, board/environment fields, operation, and a
canonical command digest. Approval records are fingerprint-bound, expiring,
single-use, and invalidated after backend restart. The generic sandbox rejects
PlatformIO upload, esptool, OpenOCD, DFU, serial-device, COM-port, and `/dev/tty`
access even if a model attempts to express them as shell commands.

The current qualification target is Windows with classic ESP32, PlatformIO
Arduino, and the `esp32dev` upload profile. Native ESP-IDF remains experimental;
additional MCU targets require their own adapters and qualification. New
hardware actions must be added as typed engine services and bound through
`backend/hardware/action_binding.py`; they must not be exposed by broadening the
terminal policy.

## Compatibility rollout

The Python AgentOrchestrator is the production run authority. The harness is an
event mirror and diagnostic host; its RPC surface does not execute agent turns or
project tools. `harness.status` publishes `runAuthority: "python-agent-orchestrator"`
so clients can identify this boundary. A future transfer of run authority is a
separate migration requiring replay parity, recovery and hardware safety evidence.

1. **Optional shadow (current):** Python owns user-visible runs; the harness mirrors and
   validates runtime events.
2. **Read authority:** session queries and projections read from the harness,
   with automatic comparison against legacy state.
3. **Turn authority:** selected non-hardware turns execute through the harness;
   Python continues to own workspace and device actions.
4. **Default authority:** the harness owns agent turns and delegates every
   embedded or physical action to typed Python services.

Promotion requires replay parity, crash/restart recovery, no invariant errors,
and the full `npm run verify:beta` gate. The status RPC reports `mode: "shadow"`
and `authoritative: false`, so no caller can mistake the current stage.

## Reviewed source provenance

The architecture was reviewed against the MIT-licensed DeepSeek Harness source
archive recorded in `third_party/deepseek-harness/PIN.json`. ForgeX does not
load code from a Downloads directory at runtime. The pin records the reviewed
version and package/lock hashes, and the upstream license is retained beside it.
