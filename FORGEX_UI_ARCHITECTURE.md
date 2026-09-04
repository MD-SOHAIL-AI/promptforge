# ForgeX V2 UI Architecture

Generated: 2026-06-17

## UI Product Direction

ForgeX should feel like a focused desktop engineering tool: dense, calm, scan-friendly, and optimized for repeated build/flash/debug workflows. Avoid marketing-style pages. The first screen should be the actual workspace or dashboard.

## Navigation

Primary activity bar:

1. Dashboard
2. Workspace
3. Project Explorer
4. Board Manager
5. Device Manager
6. Agent Console
7. Model Router
8. Build Center
9. Flash Center
10. Serial Monitor
11. Simulator
12. Logs
13. Settings

Global UI:

- Command palette.
- Status bar.
- Notifications/approvals tray.
- Workspace switcher.
- Device selector.
- Active model route indicator.

## Page Wireframes

### Dashboard

```text
┌ activity ┬──────────────── dashboard ────────────────┬ approvals ┐
│ icons    │ Recent workspaces | Devices | Builds       │ pending   │
│          │ Active agents     | Model spend            │ warnings  │
└──────────┴────────────────────────────────────────────┴───────────┘
```

### Workspace

```text
┌ activity ┬ explorer ┬ editor tabs/code ┬ inspector ┐
│ icons    │ files    │ Monaco/LSP       │ task/plan │
│          │ boards   ├──────────────────┤ pins      │
│          │ devices  │ console/serial   │ artifacts │
└──────────┴──────────┴──────────────────┴───────────┘
```

### Board Manager

```text
┌ filters ┬ board table ┬ board detail ┐
│ family  │ id/name     │ pins         │
│ sdk     │ framework   │ frameworks   │
│ plugin  │ status      │ templates    │
└─────────┴─────────────┴──────────────┘
```

### Device Manager

```text
┌ connected devices ┬ selected device detail ┬ actions ┐
│ port, board, SN   │ capabilities/history   │ monitor │
│ trust status      │ last flash/logs        │ approve │
└───────────────────┴────────────────────────┴─────────┘
```

### Agent Console

```text
┌ task graph ┬ agent transcript/events ┬ artifacts/approvals ┐
│ nodes      │ planner/build/debug      │ plan/diff/logs      │
│ status     │ tool calls               │ flash approval      │
└────────────┴──────────────────────────┴─────────────────────┘
```

### Model Router

```text
┌ providers ┬ routes by task ┬ usage/benchmarks ┐
│ keys      │ coding→Claude  │ cost/latency     │
│ health    │ reasoning→GPT  │ quality scores   │
└───────────┴────────────────┴──────────────────┘
```

### Build Center

```text
┌ build matrix ┬ output/logs ┬ artifacts ┐
│ board/env    │ compiler    │ firmware  │
│ status       │ errors      │ reports   │
└──────────────┴─────────────┴───────────┘
```

### Flash Center

```text
┌ target device ┬ firmware artifact ┬ approval/action ┐
│ port/board    │ hash/version       │ risks/confirm   │
│ confidence    │ build provenance   │ flash progress  │
└───────────────┴────────────────────┴─────────────────┘
```

### Serial Monitor

```text
┌ ports/session ┬ serial stream ┬ filters/plot/export ┐
│ baud/mode     │ timestamped   │ regex, hex, CSV     │
└───────────────┴───────────────┴─────────────────────┘
```

### Simulator

```text
┌ sim target ┬ virtual board/peripherals ┬ output ┐
│ Wokwi      │ diagram/config            │ logs   │
│ future     │ timing/probes             │ tests  │
└────────────┴───────────────────────────┴────────┘
```

### Logs

```text
┌ filters ┬ event/log table ┬ detail ┐
│ task    │ time/type       │ JSON   │
│ agent   │ severity        │ copy   │
└─────────┴─────────────────┴────────┘
```

### Settings

```text
┌ categories ┬ settings form ┬ validation/help ┐
│ models     │ fields        │ status          │
│ toolchains │ paths         │ diagnostics     │
│ hardware   │ safety        │ reset/export    │
└────────────┴───────────────┴─────────────────┘
```

## Component Hierarchy

```text
AppShell
  ActivityBar
  TopCommandBar
  WorkspaceSwitcher
  DeviceSelector
  ApprovalTray
  RoutedPage
  StatusBar

WorkspacePage
  ProjectExplorer
  EditorPane
    TabBar
    MonacoEditor
    InlineAgentActions
  WorkflowTimeline
  ConsolePanel
  InspectorPanel

AgentConsolePage
  TaskGraphView
  AgentEventStream
  ToolRunTable
  ArtifactViewer
  ApprovalRequestPanel
```

## State Architecture

Move away from one large hook into:

- `workspaceStore`
- `projectStore`
- `agentStore`
- `deviceStore`
- `boardStore`
- `modelRouterStore`
- `logStore`
- `approvalStore`

Use server state/query hooks for backend data and a WebSocket event reducer for live updates.

## Design Rules

- Keep cards for repeated items only; avoid nested cards.
- Use icons for actions and tooltips for unfamiliar controls.
- Keep board/device/build information dense and tabular.
- Make pending hardware approvals visually distinct and impossible to miss.
- Show firmware artifact hash and target device before flash.
- Keep serial/build logs copyable and filterable.

