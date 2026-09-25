# Forge Agent Redesign - Implementation Notes

## Implemented

- Capability-profile layer for Ask, Plan, Edit, Build, Build+Repair, and Simulation.
- Ask/Inspect now use the selected model and project-aware read-only tools.
- Plain Build cannot receive write tools.
- Build/repair uses compiler observations in the existing bounded autonomous loop.
- Exact Plan -> Execute binding with ID, revision, SHA-256, backend verification, and frontend transport.
- Embedded project graph injected into context.
- Wokwi simulation available as a trusted model tool after a verified build.
- Factual tool lifecycle/assistant-summary events; synthetic reasoning fallback removed from Nexus UI.
- V3 host tools validate through the tool permission policy before execution.
- Autonomous no-diff/failure stage cleanup.
- Richer persisted/restored run state.
- Existing ChangeSet/risk/reviewer/rollback and flash artifact-hash approval preserved.

## Validation performed in this package

- Python backend compilation: `python -m compileall -q backend`
- Agent-focused backend regression suite: 147 passing tests.
- Nexus/agent frontend lightweight tests: 10 passing tests.
- TypeScript parser/syntax check on changed frontend files: no parser errors.

A complete unit-suite collection in the packaging environment is blocked because `pyserial` is not installed there. ForgeX already declares `pyserial>=3.5,<4.0` in `requirements.txt`. Frontend `node_modules` are intentionally absent from the source ZIP, so a complete Next.js build also requires dependency installation on the target machine.

## Intentional compatibility choice

The redesign does not delete the existing autonomous firmware executor. It is retained underneath build/repair/simulation because that path already contains the trusted PlatformIO, ChangeSet, approval, and physical-hardware safety boundaries. The public behavior and permission model are unified first; deleting the stable executor merely to achieve a cosmetic one-class architecture would increase risk without improving the embedded-agent result.
