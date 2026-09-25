# ForgeX Harness Host

## Outcome

ForgeX uses a hybrid runtime: an Electron-owned TypeScript harness host handles
durable agent-runtime primitives, while the Python process remains the
authoritative embedded-development and physical-hardware daemon during the
compatibility rollout. This preserves the mature build, board, serial, staged
change, and flash safety paths while introducing the composable architecture
reviewed in DeepSeek Harness.

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
  |-- TypeScript Harness Host (127.0.0.1, launch token)
  `-- FastAPI Hardware Daemon (127.0.0.1, desktop token)
          |-- model-provider adapters
          |-- staged workspace / ChangeSets
          |-- PlatformIO build and verified artifacts
          `-- typed hardware services + one-time exact approvals
```

Electron starts the harness before FastAPI and injects only its loopback URL and
launch token into the backend process. FastAPI exposes authenticated status and
provider-catalog diagnostics and mirrors session creation, user messages, and
run starts. Mirror failures are observable but cannot break the authoritative
legacy run during this stage.

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

All physical mutations remain typed host operations. Flash and OTA bindings
require a verified artifact hash. Every mutation binding includes the run,
project, stable device identity, board/environment fields, operation, and a
canonical command digest. Approval records are fingerprint-bound, expiring,
single-use, and invalidated after backend restart. The generic sandbox rejects
PlatformIO upload, esptool, OpenOCD, DFU, serial-device, COM-port, and `/dev/tty`
access even if a model attempts to express them as shell commands.

Initial production targets are Windows with ESP32, Arduino Uno, and STM32. New
hardware actions must be added as typed daemon services and bound through
`backend/hardware/action_binding.py`; they must not be exposed by broadening the
terminal policy.

## Compatibility rollout

1. **Shadow (current):** Python owns user-visible runs; the harness mirrors and
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
