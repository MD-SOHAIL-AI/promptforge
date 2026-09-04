# ForgeX

ForgeX is a desktop embedded-development workspace with an AI coding agent for inspecting, editing, building, repairing, flashing, and monitoring firmware projects.


## ForgeX Nexus V4 UI/UX

ForgeX V4 is enabled by default and replaces the permanent IDE-style Explorer → Editor → AI-sidebar layout with an objective-first engineering canvas. Forge tasks are the primary workspace unit, while code, diff review, builds, hardware, serial telemetry, and the raw terminal open as adaptive canvases only when the task needs them.

V4 includes:

- **Nexus Home** with the universal Forge composer, active project, recent tasks, projects, build state, and live hardware context
- **Forge Task Canvas** with safe reasoning summaries, real agent activity, live plans, steering, Plan/Ask/Auto modes, staged-change artifacts, and hardware approvals
- **Adaptive canvases** for code, ChangeSet review, structured build artifacts, connected hardware, serial telemetry, and the engineering console
- **Forge Core** state visualization for idle, reasoning, working, building, approval, error, and verified states
- **Command Center (`Ctrl+K`)** for natural-language Forge requests and direct project/hardware commands
- **Quick Forge (`Alt+Space`)** for a context-preserving compact prompt surface
- **Focus mode (`Ctrl+Shift+F`)** that removes navigation chrome and leaves the active Forge task
- **Reasoning visibility** controls (Minimal / Normal / Detailed). The UI displays safe operational reasoning summaries and evidence, never private raw chain-of-thought.
- **Task Center** for persistent Forge sessions rather than file-tab-first navigation
- **Legacy rollback** with `NEXT_PUBLIC_FORGEX_UI_V4=0`

See [`FORGEX_NEXUS_V4.md`](FORGEX_NEXUS_V4.md) for the UI architecture and interaction model.

## Forge Agent V3

Forge Agent V3 uses a continuous model → tool → observation → model loop instead of a write-file-only generation pipeline. The agent can acquire project context on demand, stage edits safely, build firmware, reason over compiler diagnostics, repair failures, maintain a live plan, search verified project memory, load skills, and delegate bounded read-only subagents.

Core agent tools:

- `list_files`, `glob_files`, `grep_search`, `read_file`
- `write_file`, `edit_file_simple`
- `update_plan`, `load_skill`, `memory_search`, `spawn_subagent`
- `build_firmware`

Hardware remains host-controlled: verified firmware artifacts and one-time approval records are used before flashing, and serial monitor actions are routed directly to the serial service.

See [`FORGEX_AGENT_V3.md`](FORGEX_AGENT_V3.md) for the architecture and behavior.

## Main stack

- Backend: Python, FastAPI, PlatformIO integration
- Frontend: Next.js 15, React 19, TypeScript, Monaco
- Desktop shell: Electron
- Agent state: persistent sessions, run graph/events, ChangeSets, verified memory

## Requirements

Install these on the development machine:

- Python 3.10+
- Node.js 20+
- npm
- PlatformIO / PlatformIO Core for real firmware builds and uploads
- USB/serial drivers required by the target board

## Setup

From the repository root:

```bash
python -m venv .venv
```

Activate the virtual environment, then install Python dependencies:

```bash
python -m pip install -r requirements.txt
```

Install desktop dependencies:

```bash
npm install
```

Install frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

Copy `.env.example` to `.env` and configure the desired model provider/API key or local model provider.

## Run ForgeX desktop

```bash
npm run dev:desktop
```

Individual development processes are also available:

```bash
npm run dev:backend
npm run dev:frontend
npm run dev:electron
```

## Agent modes

- **Auto** — Forge may inspect, stage safe edits, build, verify, and repair automatically. Hardware flashing still requires explicit approval.
- **Ask / staged changes** — Forge prepares a ChangeSet for review rather than directly applying the project changes.
- **Plan** — read-only repository investigation and a live implementation plan; project mutation is prohibited.

## Project instructions and skills

Forge Agent reads project-local instructions from:

- `FORGEX.md`
- `AGENTS.md`

Built-in skills currently include:

- `platformio-repair`
- `serial-debug`
- `firmware-review`

Project-local skills can be added under `.forgex/skills/<skill-name>/SKILL.md`.

## Safety model

ForgeX keeps model reasoning separate from trusted host actions.

- Model file writes occur inside managed staged workspaces.
- ChangeSets capture the resulting project diff.
- Risk/review policy remains enforced before applying sensitive changes.
- Build/command capabilities are host supplied rather than arbitrary model privileges.
- Successful firmware builds produce hashable verified artifacts.
- Flash approval is bound to the selected run/board/port/environment/artifact hash.
- Serial monitor start/stop is a deterministic hardware action, not a generation workflow.

## V3 compatibility flag

Forge Agent V3 is enabled by default:

```env
FORGEX_ENABLE_AGENT_V3_LOOP=1
```

To temporarily fall back to the legacy autonomous workflow:

```env
FORGEX_ENABLE_AGENT_V3_LOOP=0
```

The existing agent orchestrator remains the host safety/state layer around the V3 reasoning loop.

## Tests

Backend:

```bash
pytest -q
```

Frontend state tests:

```bash
npm run test:ui-state
```

Electron tests after installing npm dependencies:

```bash
npm run test:electron
```

Frontend type/lint/build checks after installing `frontend/node_modules`:

```bash
cd frontend
npm run typecheck
npm run lint
npm run build
```

## Source archive policy

Release/review ZIPs should not contain `node_modules`, `.next`, `.pio`, `dist`, `.pytest_cache`, Python bytecode caches, generated firmware, local `.env` files, or runtime workspace metadata such as `workspace/external-projects.json`.
