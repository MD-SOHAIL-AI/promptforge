# ForgeX Nexus V4 UI/UX

## Product direction

ForgeX Nexus is not an IDE with an AI sidebar. The primary object is an engineering **task**: a goal Forge can investigate, plan, implement, build, repair, review, verify, and—only with explicit approval—apply to physical hardware.

The UI therefore follows this hierarchy:

```text
Intent → Forge Task → Reason / Action / Evidence → Artifacts → Code / Build / Hardware
```

Files and terminals remain available, but they are on-demand work canvases rather than permanent application chrome.

## Main surfaces

### Nexus Home

- universal Forge composer
- active project snapshot
- quick feature/debug/build actions
- connected hardware state
- recent Forge tasks
- recent/open projects

### Forge Task Canvas

- persistent session title and run state
- conversation
- safe reasoning summary
- grouped live agent activity
- real `update_plan` state
- Plan-mode approval → Auto execution
- staged ChangeSet / build artifact shortcuts
- hardware flash approval
- steering while a run is active
- reasoning display preference: Minimal / Normal / Detailed

### Adaptive work canvases

- **Code Canvas** — Monaco + optional file rail for direct manual inspection/control
- **Review Canvas** — staged file list, diff preview, safety status, ChangeSet apply action
- **Build Canvas** — structured build result, target, artifact size, warnings, firmware visualization and raw-output access
- **Hardware Canvas** — connected board, port, environment, serial state, device discovery, flash/monitor controls
- **Serial Canvas** — live telemetry/log stream plus Forge runtime-context explanation
- **Engineering Console** — terminal, problems, debug output and raw serial logs

### Command surfaces

- `Ctrl+K` — Command Center: search commands, canvases, hardware and send natural-language requests to Forge
- `Alt+Space` — Quick Forge: compact contextual composer
- `Ctrl+Shift+F` — task Focus Mode

## Reasoning UX

ForgeX V4 intentionally does **not** expose model-private chain-of-thought. It exposes safe reasoning summaries derived from the model's public turn summary and runtime evidence.

The visible model is:

```text
Reason
  Why Forge selected the next approach

Action
  Search / read / edit / build / review / hardware request

Evidence
  Tool result / diff / compiler diagnostic / verified artifact / serial output
```

Consecutive low-level read/search actions are grouped into user-meaningful activities such as `Inspected project · 4 actions`.

## Design language

- near-black graphite canvas
- one Forge intelligence accent (electric cyan) with restrained violet support
- semantic success / approval / danger colors only
- smoked-glass HUDs only for transient command and context surfaces
- edge-light / subtle glow for live intelligence and hardware states
- motion only communicates state; no decorative particles or continuous background gimmicks
- Monaco and raw terminals remain solid high-readability surfaces
- `prefers-reduced-motion` continues to override animation

## Forge Core

The abstract Forge Core is the persistent intelligence indicator:

- Idle — slow breathing ring
- Reasoning — rotating dual ring
- Working — pulse + ring motion
- Building — active pulse state
- Waiting for hardware approval — amber state
- Error — red state
- Verified — green state
- Offline — desaturated state

## Safety UX

The V4 UI surfaces the safety guarantees already enforced by Forge Agent V3:

- code edits happen in the staged overlay
- ChangeSets remain inspectable before application
- build artifacts are shown as evidence
- flash actions remain explicit hardware approvals
- the flash card displays target board/port and uses the existing artifact-bound approval backend
- serial actions are direct hardware operations, not hidden generation workflows

## Feature flag

V4 is the default UI:

```env
NEXT_PUBLIC_FORGEX_UI_V4=1
```

The previous ForgeX V3 shell remains available for rollback:

```env
NEXT_PUBLIC_FORGEX_UI_V4=0
```

## New frontend structure

```text
frontend/components/nexus/
  agent-activity.tsx
  command-center.tsx
  forge-core.tsx
  nexus-home.tsx
  nexus-primitives.tsx
  nexus-shell.tsx
  task-canvas.tsx
  task-center.tsx
  universal-composer.tsx
  work-canvases.tsx

frontend/lib/
  nexus-agent-view.ts
  nexus-agent-view.test.mts
```

Existing V3 IDE components remain intact only for legacy rollback and for reuse as on-demand code/terminal modules.
