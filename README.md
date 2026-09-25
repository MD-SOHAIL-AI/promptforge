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

## Forge Agent V3 + Harness Host

Forge Agent V3 uses a continuous model → tool → observation → model loop instead of a write-file-only generation pipeline. The agent can acquire project context on demand, stage edits safely, build firmware, reason over compiler diagnostics, repair failures, maintain a live plan, search verified project memory, load skills, and delegate bounded read-only subagents.

Core agent tools:

- `list_files`, `glob_files`, `grep_search`, `read_file`
- `write_file`, `edit_file_simple`
- `update_plan`, `load_skill`, `memory_search`, `spawn_subagent`
- `build_firmware`

Hardware remains host-controlled: verified firmware artifacts and one-time approval records are used before flashing, and serial monitor actions are routed directly to the serial service.

See [`FORGEX_AGENT_V3.md`](FORGEX_AGENT_V3.md) for the architecture and behavior.

The desktop now also owns an authenticated TypeScript harness host inspired by
the reviewed DeepSeek Harness architecture. It runs beside the Python hardware
daemon in shadow mode, durably mirrors session events, and provides the new
provider catalog, tool pipeline, plugin permissions, bounded subagents,
workflows, and fail-closed sandbox boundary. Shadow mode deliberately keeps the
proven Python orchestrator authoritative while compatibility is validated.

See [`FORGEX_HARNESS_ARCHITECTURE.md`](FORGEX_HARNESS_ARCHITECTURE.md) for the
runtime boundary, migration stages, security invariants, and source provenance.

## Main stack

- Backend: Python, FastAPI, PlatformIO integration
- Frontend: Next.js 15, React 19, TypeScript, Monaco
- Desktop shell: Electron
- Agent state: persistent sessions, run graph/events, ChangeSets, verified memory

## Requirements

Install these on the development machine:

- Python 3.10+
- Node.js 22.19+
- npm
- PlatformIO / PlatformIO Core for real firmware builds and uploads
- USB/serial drivers required by the target board

## First-time setup

From the repository root on Windows:

```bash
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm --prefix frontend ci
```

On Linux/macOS, use the equivalent `.venv/bin/python` commands. Copy `.env.example` to `.env` only if you need local overrides for model providers, ports, diagnostics, or feature flags.

You do **not** need to activate the virtual environment for normal desktop development. Electron resolves the repository Python interpreter and owns the FastAPI backend lifecycle.

## Run ForgeX desktop

From a fresh terminal, the normal development command is only:

```bash
npm run dev:desktop
```

That command starts/reuses the Next.js frontend, launches Electron, and Electron starts or safely reuses the ForgeX FastAPI backend. Manual `.venv` activation and a separate `npm run dev:backend` are not required.

Individual development processes are also available:

```bash
npm run dev:backend
npm run dev:frontend
npm run dev:electron
```

The standalone backend requires `FORGEX_LOCAL_TOKEN` (at least 32 characters)
for API and WebSocket operations. `npm run dev:desktop` provisions this token and
the desktop session cookie automatically. A backend started without a token
serves `/health` only.

Firmware flashing runs through a Forge task's verified build, prepare, and
one-time confirmation flow. The legacy direct `POST /flash` endpoint is disabled.

## NVIDIA NIM provider

Open **Settings -> Models -> Cloud APIs -> NVIDIA NIM**, paste the API key
created in the NVIDIA API Catalog into **API key**, and select **Save provider**.
Use **Models** to refresh NVIDIA's model list, choose a NVIDIA NIM model, then
select **Use this model**. Provider credentials and model catalogs are kept
separate from every other provider.

For environment-based development configuration, copy `.env.example` to
`.env` and set `NVIDIA_API_KEY` and, optionally, `NVIDIA_MODEL`. The hosted API
defaults to `https://integrate.api.nvidia.com/v1`; a self-hosted NIM can be used
by changing its Base URL in the same settings panel.

NVIDIA requests use a strict 45-second deadline and Forge Agent applies a
60-second whole-turn budget, including schema repair and fallback. Override the
request deadline with `FORGEX_NVIDIA_NIM_TIMEOUT_SECONDS` (5-180 seconds) when a
self-hosted deployment has different cold-start characteristics. ForgeX never
disables the NIM deadline.

Long NIM turns are protected by a fenced session-lease heartbeat. The runtime
renews ownership while a model or hardware step is active and fails closed if
another worker takes the lease; an expired owner is never revived.

## Model providers

ForgeX includes DeepSeek, Anthropic, OpenAI, OpenRouter, Groq, Cerebras, NVIDIA NIM,
Gemini, Ollama, and LM Studio adapters. Settings also supports custom OpenAI
Chat Completions, OpenAI Responses, and Anthropic Messages endpoints. Custom
provider definitions are persisted separately from credentials; secrets stay
in the operating-system credential store. A custom provider cannot be removed
while any route still references it.

To use Cerebras, open **Settings -> Models -> Cloud APIs -> Cerebras**, enter
your API key, and select **Save provider**. ForgeX imports every model returned
by Cerebras for that key from `/v1/models`; choose one under **Active model**
and select **Use this model**. You can refresh the account's model list with
**Models**. For environment configuration, set `CEREBRAS_API_KEY` and optionally
`CEREBRAS_MODEL` in a private `.env` file.

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

## Agent runtime flags

Forge Agent V3 is enabled by default:

```env
FORGEX_ENABLE_AGENT_V3_LOOP=1
```

Desktop release mode requires the V3 loop. If `FORGEX_DESKTOP=1`, setting this flag to `0` is rejected at backend startup.

The legacy fallback is only for non-desktop diagnostic runs:

```env
FORGEX_ENABLE_AGENT_V3_LOOP=0
```

The existing agent orchestrator remains the host safety/state layer around the V3 reasoning loop.

## Tests

Backend:

```bash
.venv\Scripts\python.exe -m pytest -q
```

Frontend state tests:

```bash
npm run test:ui-state
```

Harness host tests:

```bash
npm run test:harness
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

Run the complete production-beta gate from the repository root:

```bash
npm run verify:beta
```

## Source archive policy

Release/review ZIPs should not contain `node_modules`, `.next`, `.pio`, `dist`, `.pytest_cache`, Python bytecode caches, generated firmware, local `.env` files, or runtime workspace metadata such as `workspace/external-projects.json`.

Use the strict scanner against actual staging folders or ZIPs:

```bash
npm run scan:release -- path\to\forgex-release.zip
```

For the local development checkout, use the source-tree scan. It ignores disposable generated artifacts while still checking source paths:

```bash
npm run scan:source-release
```


## Forge Agent redesign validation

See [`FORGEX_AGENT_REDESIGN_VALIDATION.md`](FORGEX_AGENT_REDESIGN_VALIDATION.md) for the implemented capability model, final regression results, and local provider/frontend verification commands.
