# ForgeX Agent Redesign — Final Validation

> **Historical validation snapshot.** For the final hardened package, use `FORGEX_RELEASE_HARDENING_VALIDATION.md`.


This package contains the hardened Forge Agent redesign built around one model/tool execution experience with capability profiles instead of separate chat/edit/build brains.

## Implemented core

- Model-driven Ask/Inspect using read-only tools.
- Plan mode with immutable `plan_id` / revision / SHA-256 binding and explicit approved-plan execution.
- Edit mode with staged writes and strict `apply_patch` support.
- Strict Build-only capability that cannot write files.
- Build+Repair capability with bounded edit/build feedback.
- Simulation capability using the existing trusted Wokwi path without physical-hardware authority.
- Embedded Project Graph V2: PlatformIO board/framework/environment, GPIO symbols/modes, I2C addresses, buses/features, and conservative classic-ESP32 electrical/pin warnings.
- Safe model summary streaming from OpenAI-compatible SSE providers. Raw ToolPlan JSON and hidden reasoning are not exposed to the UI.
- First-class factual tool/build/simulation activity events for the Nexus timeline.
- Capability enforcement at the ProductAgentService boundary as well as the tool layer.
- Precise unified-diff patching with exact context matching; stale/fuzzy patches are rejected.
- Stage cleanup and richer persisted recovery state.
- Existing ChangeSet, risk review, rollback, steering, skills, memory, bounded subagents, firmware hashing, one-time flash approval and pre-flash hash verification preserved.
- Arbitrary `run_command` remains unavailable to the redesigned core capability profiles.
- Graceful `pyserial` compatibility: source/test environments without pyserial can load ForgeX, while real serial/hardware operations fail explicitly until the declared dependency is installed.

## Final regression results

- `pytest -q`: **1370 passed**.
- `python -m compileall -q backend scripts tests`: **pass**.
- Focused Nexus TypeScript behavior tests: **6 passed** using Node's native type stripping.
- Modified frontend TypeScript/TSX syntax/transpile validation: **11/11 pass**.
- Deterministic Forge Agent acceptance benchmarks are included under `tests/benchmarks/`.

## External validation that requires your machine

Two checks depend on resources intentionally unavailable in the packaging sandbox:

1. **Full Next.js production build** — run after restoring frontend dependencies. The npm registry was unreachable/incomplete during packaging, so `node_modules` is not bundled.
2. **Real cloud-provider smoke** — requires your own API key and network. Use:

```bash
python scripts/forge_agent_provider_smoke.py --provider openrouter
```

or select `openai`, `groq`, or `gemini`. The script prints safe provider diagnostics and never prints the key or raw provider response.

## Recommended local verification

```bash
python -m pip install -r requirements.txt
pytest -q

cd frontend
npm ci
npm run typecheck
npm run build
```

Then start ForgeX normally and run a real task through the selected provider. For physical hardware, keep the existing approval/hash workflow enabled.
