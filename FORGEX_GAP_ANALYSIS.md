# ForgeX V1 Gap Analysis

Generated: 2026-06-17

## Benchmark Sources Checked

- VS Code documentation: https://code.visualstudio.com/docs
- Claude Code documentation: https://code.claude.com/docs/en/overview
- OpenCode documentation: https://opencode.ai/docs/
- OpenCode product page: https://opencode.ai/
- PlatformIO IDE for VS Code documentation: https://docs.platformio.org/en/latest/integration/ide/vscode.html
- Cursor docs/product pages were checked at `cursor.com/docs`; public pages are client-rendered in the fetch view, so capability comparison also uses current public descriptions from search-visible Cursor documentation/product metadata.
- Google Antigravity official site was checked at https://antigravity.google/; public fetch returned no text, so capability comparison uses current public launch coverage and product descriptions available in search-visible sources.

## Current ForgeX V1 Baseline

V1 has a working backend-centered embedded workflow:

- Prompt to deterministic plan.
- LLM project generation.
- Project validation.
- PlatformIO build.
- Board detection.
- Flash operation.
- Serial monitoring.
- Wokwi simulation integration.
- WebSocket progress.
- Next.js Monaco workspace UI.

It is closer to a web EDE workflow prototype than a Cursor/VS Code-class desktop IDE.

## Capability Matrix

| Capability | ForgeX V1 | VS Code | Cursor | Claude Code | OpenCode | Antigravity | PlatformIO IDE | Gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Desktop shell | No | Yes | Yes | CLI/desktop/web/IDE | TUI/desktop/IDE | Yes | VS Code extension | Critical |
| Rich editor platform | Monaco only | Full IDE | Full AI IDE | IDE plugin/CLI | IDE/TUI | IDE | VS Code | Critical |
| Extension marketplace | No | Yes | VS Code-compatible/history | MCP/hooks/skills | plugins/tools/MCP | plugins/agent manager | PlatformIO ecosystem | Critical |
| Embedded board manager | Static JSON | Extension-driven | Generic | Generic | Generic | Generic | Strong | Critical |
| Device manager | Minimal detection | Extensions | Generic | Tool use | Tool use | Browser/terminal/editor tools | Strong serial/ports | Critical |
| Model router | Single env provider | AI provider support | Built-in | Anthropic/3P | Many providers | Gemini/3P | N/A | High |
| Agent mode | Deterministic planner/coordinator | Agents | Agents | Agentic loop | Plan/Build modes | Multi-agent manager | N/A | High |
| Multi-agent orchestration | No | Emerging | Cloud/background agents | multiple/background agents | multi-session/agents | manager view | N/A | High |
| Approval controls | Informal | IDE trust model | Tool approvals | permission modes | permissions/policies | trust artifacts | Manual actions | Critical for hardware |
| Terminal integration | Backend subprocess only | Integrated terminal | Integrated terminal | Native terminal | Native terminal | Integrated terminal | VS Code terminal | High |
| Git/SCM | No | Strong | Strong | commits/PRs | GitHub/GitLab | IDE SCM | VS Code SCM | Medium |
| Debugging | Failure classifier only | Full debugger | AI/debug | command/test debug | tool debug | agent debug | Embedded debug | Critical |
| Simulation | Wokwi tool | Extensions | Generic | Generic | Generic | Browser/tool validation | Wokwi possible | Medium |
| Serial monitor | Backend service/tool | Extensions | Generic | Shell tools | Shell tools | Terminal tools | Strong | Medium |
| LSP/intellisense | Monaco syntax only | Strong | Strong | IDE host | LSP support | IDE host | Strong C/C++ | Critical |
| Project templates | LLM-generated | Extension templates | Generic | Generated | Generated | Generated | Strong board templates | High |
| Persistent sessions | Limited logs/history | Workspaces | Agent/session history | sessions/memory | sessions/share | artifacts/history | Workspaces | High |
| Enterprise policy | No | Policies | Team/admin | Admin/settings | Enterprise/policies | likely workspace policy | N/A | High |
| Cloud sync | No | Settings sync | Cloud agents | web/remote/scheduled | share/session | cloud/manager | N/A | High |

## Missing Capabilities By Product Benchmark

### VS Code

ForgeX lacks:

- Electron app shell.
- Workbench-grade command palette, settings, keybindings, activity bar, panels, terminals.
- Extension API and marketplace.
- Debug Adapter Protocol integration.
- Language Server Protocol integration for C/C++/PlatformIO.
- Source control view.
- Tasks/launch configurations.
- Remote development.
- Enterprise policy controls.

### Cursor

ForgeX lacks:

- AI-native editor interactions: inline edits, multi-file smart rewrites, codebase chat, file mentions.
- Background/cloud agent workflows.
- Rules/instructions system scoped by project/path.
- Fast codebase indexing and semantic retrieval.
- AI diff review UX.
- AI autocomplete.
- Existing VS Code ecosystem compatibility.

### Claude Code

ForgeX lacks:

- Agentic loop that reads files, edits files, runs commands, observes output, and iterates.
- Permission modes and auditable tool approvals.
- Persistent instruction/memory files.
- MCP integration.
- Hooks and skills.
- Multi-agent/subagent workflows.
- CLI composability and CI integration.
- Git commit/PR automation.

### OpenCode

ForgeX lacks:

- Terminal-native TUI.
- Plan mode vs build mode.
- Multi-session agents on the same project.
- Broad provider configuration and local models.
- AGENTS.md-style project initialization.
- LSP loading for model context.
- Custom tools, rules, policies, commands, formatters, MCP servers, SDK/server/plugin model.
- Shareable sessions.

### Google Antigravity

ForgeX lacks:

- Agent Manager for parallel asynchronous agents.
- Artifact-based verification: plans, task lists, screenshots, recordings, validation artifacts.
- Integrated browser validation.
- Agent memories/learned workflows.
- Multi-workspace orchestration.
- First-class agent-first product surface.

### PlatformIO IDE

ForgeX lacks:

- Board/package/library registry management.
- Platform and framework installation UX.
- Project wizard/templates.
- Library manager.
- Embedded debugging.
- Test runner.
- Serial plotter/monitor maturity.
- PlatformIO home/dashboard equivalent.
- Board-specific upload/debug configuration UX.

## Embedded-Specific Gaps

Critical missing V2 features:

- Device registry with trusted devices, ports, serial numbers, VID/PID, board match confidence, last flash, baud defaults, and safety notes.
- Explicit human approval gate for flash, erase, fuse/bootloader, OTA, and high-voltage/debug operations.
- Board SDK/toolchain registry.
- Board plugin packaging.
- Build matrix by board/environment/framework.
- Embedded test runner and hardware-in-loop jobs.
- Debug adapter support: OpenOCD, ST-Link, J-Link, CMSIS-DAP, ESP-PROG.
- Serial terminal with binary/hex modes, timestamps, filters, plotting, and capture export.
- Simulation center with Wokwi plus future QEMU/Renode.
- Pin planner and peripheral conflict resolver UI.
- BOM/library dependency resolver.

## Architecture Gaps

P0:
- Desktop shell and IPC permissions.
- Model router with persistent configuration and telemetry.
- Agent runtime with approvals and durable event log.
- Board/device registries.
- Repository hygiene cleanup.

P1:
- LSP/DAP integration.
- Settings and command palette.
- Workflow graph execution and resumability.
- Frontend route architecture for V2 pages.

P2:
- Marketplace/cloud sync/enterprise.

## Recommendation

Do not rewrite V1. Keep FastAPI, services, validators, tools, runtime results, and tests. Add Electron and V2 product surfaces around the existing backend, then gradually introduce model router, board registry, device manager, and autonomous agents behind stable interfaces.

