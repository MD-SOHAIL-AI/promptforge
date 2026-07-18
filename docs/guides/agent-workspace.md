# Agent Workspace and Provider Setup

Use Connections to sign in, store API keys in the OS credential store, inspect local endpoints, refresh health, and disconnect named accounts. Connected, Authenticated, Enabled, Healthy, Policy Eligible, and Production Eligible are separate states.

Use Agent Workspace for durable runs. Select a published Agent Profile, optionally choose an advanced per-run model override, review bounded context disclosure, confirm the route, and follow backend-authorized actions. Conversation, plan, events, approvals, artifacts, execution status, and verification reports come from the backend and restore after reconnect.

Codex CLI and AGY use their official CLI-owned authentication. ForgeX does not read their token files. They are AgentAdapters, not model providers, and can only produce contained review proposals. ForgeX owns apply, build, flash, monitor, and approval.

The temporary legacy panels remain disabled by default because runtime parity has not passed. Administrators should not enable them for new workflows. Existing JSONL workflow data remains readable during migration but is not the target authority.

Safe support bundles are available from GET /diagnostics/export. They contain aggregate state and explicitly omit credentials, raw authentication output, environment secrets, prompts, responses, source, diffs, and raw logs.
