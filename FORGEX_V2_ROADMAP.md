# ForgeX V2 Implementation Roadmap

Generated: 2026-06-17

## Phase 1: Desktop Foundation

Goals:
- Add Electron shell around existing Next.js + FastAPI.
- Supervise backend process.
- Add desktop settings path and app lifecycle.
- Preserve standalone backend/frontend dev mode.

Files impacted:
- new `electron/*`
- `frontend/next.config.ts`
- `backend/api/app.py`
- new `backend/settings/*`
- packaging configs

Risks:
- Process lifecycle on Windows/macOS/Linux.
- Localhost port conflicts.
- Native serial/USB permissions.

Milestones:
- Electron launches UI.
- Backend starts/stops cleanly.
- App can execute existing workflow.

Estimated effort: 2-3 weeks.

## Phase 2: Model Router

Goals:
- Add provider/model registry.
- Support OpenAI, Anthropic, Gemini, OpenRouter, Ollama, LM Studio.
- Add fallback, token/cost/latency tracking.

Files impacted:
- `backend/services/llm_service.py`
- new `backend/model_router/*`
- new DB migrations
- `frontend` settings/model router pages

Risks:
- Provider API differences.
- Cost accuracy.
- Local model availability.

Milestones:
- Existing code generation uses router.
- Model Router UI shows providers/routes/usage.

Estimated effort: 3-4 weeks.

## Phase 3: Board System

Goals:
- Introduce board registry.
- Add board plugin schema.
- Add SDK registry and board templates.
- Expand supported boards.

Files impacted:
- `backend/hardware/boards/*`
- `backend/hardware/compatibility/*`
- `backend/hardware/constraints/*`
- `backend/validation/board_validator.py`
- new `backend/boards/*`
- Board Manager UI

Risks:
- Board metadata correctness.
- Plugin security.
- PlatformIO mapping drift.

Milestones:
- Registry wraps existing boards.
- ESP32 variants/RP2040 added.
- Board Manager reads registry.

Estimated effort: 4-6 weeks.

## Phase 4: Agent Framework

Goals:
- Add agent sessions and task graph.
- Implement Planner, Hardware, Firmware, Build, Debug, Flash, Test, Monitor, Docs, Knowledge agents as typed roles.
- Add durable events.

Files impacted:
- `backend/agent/*`
- `backend/workflow/*`
- `backend/main.py`
- new `backend/agents/*`
- new `backend/events/*`
- Agent Console UI

Risks:
- Overlapping with existing workflow runner.
- Tool permission errors.
- Long-running session recovery.

Milestones:
- Task graph can run current workflow.
- Events are durable/replayable.
- Agent Console displays graph and tool runs.

Estimated effort: 5-7 weeks.

## Phase 5: Autonomous Workflows

Goals:
- Add build-fix-rerun loop.
- Add validation gates.
- Add approval service for hardware actions.
- Enforce no-flash-without-approval.

Files impacted:
- `backend/tools/flash_firmware.py`
- `backend/api/routes/flash.py`
- `backend/workflow/generate_code_handler.py`
- validators
- new `backend/approvals/*`
- Flash Center and Approval Tray UI

Risks:
- Safety policy bypass.
- UI/agent state mismatch.
- Hardware damage if approvals are weak.

Milestones:
- Flash requires approval token.
- Artifact hash/board/port tied to approval.
- Agent can recover from build failures.

Estimated effort: 4-6 weeks.

## Phase 6: UI Modernization

Goals:
- Build V2 pages: Dashboard, Workspace, Project Explorer, Board Manager, Device Manager, Agent Console, Model Router, Build Center, Flash Center, Serial Monitor, Simulator, Logs, Settings.
- Refactor frontend state.
- Add command palette and settings.

Files impacted:
- `frontend/app/*`
- `frontend/components/*`
- `frontend/hooks/*`
- `frontend/lib/*`
- `frontend/types/index.ts`

Risks:
- Large UI state migration.
- Regression in current workspace flow.
- Accessibility and layout complexity.

Milestones:
- V2 shell navigation.
- Existing workflow still works.
- Board/device/model pages read real backend APIs.

Estimated effort: 6-8 weeks.

## Phase 7: Simulation

Goals:
- Expand Wokwi integration.
- Add simulator center.
- Add simulation artifacts and validation reports.
- Prepare future QEMU/Renode adapters.

Files impacted:
- `backend/tools/wokwi_simulator.py`
- `backend/runtime/result.py`
- `backend/services/artifact_service.py`
- new `backend/simulation/*`
- Simulator UI

Risks:
- External simulator CLI/API changes.
- Test determinism.
- Licensing/tool availability.

Milestones:
- Wokwi run visible in UI.
- Simulation logs/artifacts stored.
- Test Agent can use simulation result.

Estimated effort: 3-5 weeks.

## Phase 8: Marketplace

Goals:
- Add board/template/tool/agent plugin marketplace.
- Local plugin install/update/uninstall.
- Plugin security model.

Files impacted:
- new `backend/plugins/*`
- `backend/boards/*`
- `backend/tools/tool_registry.py`
- Settings/Marketplace UI

Risks:
- Supply chain security.
- Version compatibility.
- Sandboxing.

Milestones:
- Local plugin manifest loads.
- Board plugin installed from local path.
- Marketplace metadata displayed.

Estimated effort: 5-7 weeks.

## Phase 9: Cloud Sync

Goals:
- Sync settings, workspaces metadata, board plugins, model routes, session summaries.
- Optional cloud agent handoff later.

Files impacted:
- new `backend/sync/*`
- DB migrations
- Electron auth/settings
- frontend sync settings

Risks:
- Privacy expectations for code.
- Conflict resolution.
- Offline-first correctness.

Milestones:
- Settings sync.
- Workspace metadata sync.
- Explicit opt-in for any code/artifact sync.

Estimated effort: 6-8 weeks.

## Phase 10: Enterprise Features

Goals:
- Policies, audit logs, provider allowlists, hardware action governance.
- Team settings and shared board/plugin catalogs.
- Exportable compliance logs.

Files impacted:
- `backend/settings/*`
- `backend/approvals/*`
- `backend/events/*`
- `backend/model_router/*`
- `backend/plugins/*`
- Admin/settings UI

Risks:
- Policy bypass.
- Audit log integrity.
- Multi-user identity model.

Milestones:
- Policy engine gates providers/tools/hardware actions.
- Audit export.
- Enterprise settings import/export.

Estimated effort: 8-12 weeks.

