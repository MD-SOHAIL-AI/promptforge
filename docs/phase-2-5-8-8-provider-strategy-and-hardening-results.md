# Phase 2.5.8.8 Provider Strategy and Hardening Results

## Result

The provider strategy layer is default-deny and evidence-backed. It records provider kind, transport, operational state, write evidence, review eligibility, production eligibility, and safe failure reason without storing model instructions or provider output.

Local CLI providers are paused because AGY and Codex failed safe write-provider validation.

| Provider | State | Execution | Routing | Reason |
| --- | --- | --- | --- | --- |
| AGY | paused | denied | denied | unreliable workspace and exact scratch output |
| Codex CLI | paused | denied | denied | permission/workspace blocked under safe policy |
| Claude CLI | disabled | denied | denied | not investigated or approved |
| OpenCode | reference only | denied | denied | provider integration forbidden |
| API-backed provider | design candidate | denied | denied | ForgeX-owned runtime required |

Detection, installation, authentication, or headless-mode discovery alone cannot make a provider routeable. Review eligibility requires exact safe output evidence, unchanged active workspace and sandbox marker, no link/reparse output, and no persisted raw prompt or provider output. Production eligibility additionally requires hardening evidence and is forbidden for local CLI providers.

The next implementation phase is the ForgeX-owned tool runtime with a fake structured-plan provider.
