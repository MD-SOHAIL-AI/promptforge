<div align="center">

<!-- Codex OAuth is QA-only: one shared resolved official CLI status service gates one external disposable
sandbox smoke. Exact pass creates a Bridge Review; tokens/auth/browser URLs are
not read, strict content validation permits only BOM/newline normalization, and
an explicit safe pass predicate gates review creation. Production routing,
auto-apply, build, and flash stay disabled. -->

<h1>
  <br/>
  ⚡ PromptForge
  <br/>
</h1>

<h3>AI-Powered Embedded Development Environment</h3>

<p>
  Natural language → firmware. Plan, generate, build, flash, simulate, and debug — from a single interface.
</p>

<p>
  <img src="https://img.shields.io/badge/status-active%20development-blue?style=flat-square" alt="Status" />
  <img src="https://img.shields.io/badge/tests-1700%2B-brightgreen?style=flat-square" alt="Tests" />
  <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/next.js-15.5-black?style=flat-square&logo=next.js" alt="Next.js" />
  <img src="https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/PlatformIO-integrated-orange?style=flat-square&logo=platformio&logoColor=white" alt="PlatformIO" />
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square" alt="License" />
  </a>
</p>

<p>
  <a href="#-overview">Overview</a> ·
  <a href="#-features">Features</a> ·
  <a href="#-architecture">Architecture</a> ·
  <a href="#-installation">Installation</a> ·
  <a href="#-usage">Usage</a> ·
  <a href="#-roadmap">Roadmap</a> ·
  <a href="#-contributing">Contributing</a>
</p>

---

</div>

## Overview

PromptForge is an **AI-powered Embedded Development Environment (EDE)** that collapses the embedded development loop into a single, unified interface. Describe what you want in plain language; PromptForge handles planning, code generation, compilation, validation, flashing, and simulation — with full real-time observability at every stage.

It is designed for engineers who want to move fast without sacrificing correctness. Every code path passes through a deterministic validation pipeline before it reaches hardware. Every execution step is streamed back to the UI in real time.

**Current status:** active development. The ForgeX-owned agent runtime is integrated behind disabled-by-default product flags. The Codex CLI subscription bridge has a separate experimental QA-only path; it is never product-routeable and does not change the paused status of normal local CLI providers. Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM are registered as disabled-by-default API planner providers for the product runtime.

**Agent runtime provider policy:** models plan; ForgeX acts. API planners may return strict ToolPlan JSON only. ForgeX validates the plan, executes allowed tools in a managed sandbox, captures the diff, creates a Bridge Review, and requires explicit user apply. The Codex subscription bridge instead delegates authentication and model execution to the official CLI while ForgeX retains the external sandbox, diff, review, and apply boundary. It requires `--confirm-real-codex --subscription-bridge-retry`, uses `C:\forgex-codex-sandboxes`, and remains QA-only with no auto-apply/build/flash. AGY and normal Codex local CLI routing are paused, Claude CLI is disabled, and OpenCode is reference-only.

**AGY scratch project import:** AGY remains paused as a direct editor. With `FORGEX_ENABLE_AGY_SCRATCH_IMPORT=1`, a user may paste one exact AGY-generated scratch project folder into the Agent panel. ForgeX validates only that selected tree, blocks secrets/binaries/link escapes and bounded-limit violations, copies safe text project files to a managed import sandbox, and creates a review. It never discovers the newest scratch folder and never auto-applies, builds, or flashes.

**AGY assisted generation:** `FORGEX_ENABLE_AGY_ASSISTED_RUNNER=1` exposes one experimental **ESP32 PlatformIO Blink** template. An explicit click runs AGY from `C:\forgex-agy-runs`, checks only a precomputed expected scratch folder, reuses the manual importer, and creates a review after exact validation. If the expected folder is absent, ForgeX performs no import and offers the pasted-path fallback. AGY never runs in or edits the active workspace directly.

---

## Screenshots

> **Note:** Screenshots will be added upon first stable release. Placeholder sections are provided below.

| Workspace Explorer | Workflow Inspector | Build Log |
|---|---|---|
| `[screenshot]` | `[screenshot]` | `[screenshot]` |

| Monaco Editor | Flash Progress | Wokwi Simulation |
|---|---|---|
| `[screenshot]` | `[screenshot]` | `[screenshot]` |

---

## Features

### Core Workflow

| Stage | Description |
|---|---|
| **Plan** | LLM decomposes the natural-language prompt into a deterministic execution plan |
| **Generate** | Code generation service produces board-aware, PlatformIO-compatible firmware |
| **Validate** | Multi-layer validation pipeline: syntax, safety, constraint, and hardware rules |
| **Build** | PlatformIO toolchain compiles the firmware; artifacts are stored and versioned |
| **Flash** | One-click flashing to connected hardware with port auto-detection |
| **Monitor** | Live serial monitor streamed to the UI over WebSocket |
| **Simulate** | Wokwi-backed simulation for hardware-free iteration |

### Platform Support

| Board | Framework | Simulation |
|---|---|---|
| ESP32 | Arduino / ESP-IDF | ✅ Wokwi |
| ESP32-S3 | Arduino / ESP-IDF | ✅ Wokwi |
| ESP32-C3 | Arduino / ESP-IDF | ✅ Wokwi |
| STM32 (Nucleo / BluePill) | Arduino / STM32Cube | 🔜 Planned |
| Arduino Uno | Arduino | ✅ Wokwi |

### Engineering Highlights

- **Hardware-aware generation** — board-specific pin maps, peripheral constraints, and framework idioms are injected at generation time.
- **Validation pipeline** — firmware clears safety, constraint, and firmware-correctness checks before any build is attempted.
- **Deterministic retry engine** — transient LLM or build failures are caught, classified, and retried with stage-aware context.
- **Streaming execution** — every pipeline stage emits structured events over WebSocket; the UI updates in real time.
- **Workspace manager** — projects, artifacts, and build history are isolated per workspace and fully restorable.
- **Monaco editor** — full IntelliSense-class editing for generated or manually authored firmware.

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                          Browser                               │
│                                                                │
│  ┌─────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│  │  Workspace  │  │ Workflow Inspector│  │  Monaco Editor  │  │
│  │  Explorer   │  │  (live events)   │  │  (firmware src) │  │
│  └──────┬──────┘  └────────┬─────────┘  └────────┬────────┘  │
│         └──────────────────┴────────────────────┘            │
│            Okay, thanks.                │  Next.js / TypeScript            │
└────────────────────────────┼──────────────────────────────────┘
                             │  HTTP + WebSocket
┌────────────────────────────┼──────────────────────────────────┐
│                       FastAPI Layer                            │
│                            │                                   │
│                      ┌─────▼──────┐                           │
│                      │  Planner   │  ← OpenRouter LLM         │
│                      └─────┬──────┘                           │
│                            │                                   │
│                  ┌─────────▼──────────┐                       │
│                  │ Workflow Coordinator│                       │
│                  └─────────┬──────────┘                       │
│                            │                                   │
│           ┌────────────────┼────────────────┐                 │
│           │                │                │                  │
│    ┌──────▼──────┐  ┌──────▼──────┐  ┌─────▼──────┐         │
│    │  Code Gen   │  │  Validation  │  │  Workspace │         │
│    │  Service    │  │  Pipeline    │  │  Manager   │         │
│    └──────┬──────┘  └─────────────┘  └────────────┘         │
│           │                                                    │
│    ┌──────▼──────────────────────────────────┐               │
│    │           PlatformIO Toolchain           │               │
│    └──────┬──────────┬──────────┬────────────┘               │
│           │          │          │                              │
│        Build       Flash     Monitor                          │
│           │          │          │                              │
│    ┌──────▼──────────▼──────────▼────────────┐               │
│    │         Hardware / Wokwi Simulator       │               │
│    └──────────────────────────────────────────┘               │
└────────────────────────────────────────────────────────────────┘
```

---

## Installation

### Prerequisites

| Dependency | Minimum Version | Notes |
|---|---|---|
| Python | 3.11 | |
| Node.js | 18 LTS | |
| PlatformIO Core | 6.x | `pip install platformio` |
| Git | 2.x | |

### 1. Clone the Repository

```bash
# Obtain the ForgeX source from your configured repository, then:
cd promptforge
```

### 2. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install PlatformIO (if not already present)
pip install platformio
```

### 3. Frontend Setup

```bash
cd frontend

npm install
```

---

## Environment Setup

### Backend — `.env`

Copy the example file and fill in your values:

```bash
cp backend/.env.example backend/.env
```

```dotenv
# ── OpenRouter ──────────────────────────────────────────
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# ── Model routing ───────────────────────────────────────
PRIMARY_MODEL=nvidia/llama-3.1-nemotron-ultra-253b-v1:free
FAST_MODEL=deepseek/deepseek-v3-0324:free

# ── Server ──────────────────────────────────────────────
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000What is this? What is this? What is this?I'll leave you in a second.

# ── Storage ─────────────────────────────────────────────
WORKSPACE_ROOT=./workspaces
ARTIFACT_ROO
T=./artifacts

# ── Runtime ─────────────────────────────────────────────
MAX_RETRY_ATTEMPTS=3
SERIAL_TIMEOUT=30
```

### Frontend — `.env.local`

```bash
cp frontend/.env.local.example frontend/.env.local
```

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

## OpenRouter Configuration

PromptForge uses a two-model routing strategy:

| Role | Model | Purpose |
|---|---|---|
| Primary | `nvidia/llama-3.1-nemotron-ultra-253b-v1:free` | Deep planning and code generation |
| Fast path | `deepseek/deepseek-v3-0324:free` | Failure classification, quick re-runs |

To use commercial models, replace the `*_MODEL` values in `.env` with any model available on [openrouter.ai](https://openrouter.ai/models). The client is provider-agnostic.

```python
# backend/core/llm_client.py — model routing is configured here
PRIMARY_MODEL   = os.getenv("PRIMARY_MODEL")
FAST_MODEL      = os.getenv("FAST_MODEL")
```

---

## Running the Backend

```bash
cd backend
source .venv/bin/activate

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`.
Interactive API docs: `http://localhost:8000/docs`

---

## Running the Frontend

```bash
cd frontend

npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

### Production Build

```bash
npm run build
npm start
```

---

## Usage

### 1. Create a Workspace

Open the Workspace Explorer and click **New Workspace**. Select your target board from the dropdown.

### 2. Enter a Prompt

Type a plain-language description of the firmware you want in the prompt input.

### 3. Execute the Pipeline

Click **Forge**. The Workflow Inspector shows live progress across each stage: Plan → Generate → Validate → Build → (Flash / Simulate).

### 4. Review and Edit

Generated firmware opens in the Monaco Editor. You can edit it manually before re-running any stage.

### 5. Flash or Simulate

- **Flash:** connect your board via USB, select the detected port, and click **Flash**.
- **Simulate:** click **Simulate** to launch Wokwi (no hardware required).

---

## Example Prompts

```
Blink the built-in LED of an ESP32 every 500 ms using FreeRTOS tasks.
```

```
Read temperature and humidity from a DHT22 sensor on GPIO 4.
Print the values to serial every 2 seconds.
```

```
Connect an ESP32-S3 to a WiFi network and POST a JSON payload
to https://example.com/api/data every 10 seconds.
```

```
Drive a 28BYJ-48 stepper motor with ULN2003 driver on an Arduino Uno.
Complete one full revolution clockwise, pause 1 s, then reverse.
```

```
Configure UART2 on STM32 at 115200 baud. Echo any received byte back
to the sender and toggle an LED on each received character.
```

---

## Supported Hardware

PromptForge validates board-specific constraints at the planning stage. Each board profile encodes pin availability, peripheral support, flash/RAM limits, and framework compatibility.

| Board | MCU | Flash | RAM | Framework |
|---|---|---|---|---|
| ESP32 DevKit v1 | Xtensa LX6 dual-core | 4 MB | 520 KB | Arduino / ESP-IDF |
| ESP32-S3 DevKitC | Xtensa LX7 dual-core | 8 MB | 512 KB | Arduino / ESP-IDF |
| ESP32-C3 SuperMini | RISC-V single-core | 4 MB | 400 KB | Arduino / ESP-IDF |
| STM32F103C8 (BluePill) | ARM Cortex-M3 | 64 KB | 20 KB | Arduino / STM32Cube |
| STM32F401 (Nucleo-64) | ARM Cortex-M4 | 256 KB | 64 KB | Arduino / STM32Cube |
| Arduino Uno R3 | ATmega328P | 32 KB | 2 KB | Arduino |

---

## Project Structure

```
promptforge/
├── backend/
│   ├── main.py                   # FastAPI entry point
│   ├── requirements.txt
│   ├── .env.example
│   ├── core/
│   │   ├── engine.py             # 🔒 Workflow orchestration (locked)
│   │   ├── session.py            # 🔒 Session state (locked)
│   │   ├── retry.py              # 🔒 Retry engine (locked)
│   │   └── subprocess_mgr.py     # 🔒 Process management (locked)
│   ├── services/
│   │   ├── planner.py            # Prompt → execution plan
│   │   ├── code_gen.py           # Firmware generation service
│   │   ├── validator.py          # Multi-layer validation pipeline
│   │   ├── failure_classifier.py # Stage-aware failure classification
│   │   ├── serial_runtime.py     # Asyncio serial port lifecycle
│   │   └── workspace_mgr.py      # Workspace and artifact management
│   ├── boards/
│   │   ├── esp32.json
│   │   ├── esp32s3.json
│   │   ├── esp32c3.json
│   │   ├── stm32.json
│   │   └── arduino_uno.json
│   ├── api/
│   │   ├── routes/
│   │   └── websocket.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── api/
│       └── workflow/
├── frontend/
│   ├── src/
│   │   ├── app/                  # Next.js app router
│   │   ├── components/
│   │   │   ├── Editor/           # Monaco editor wrapper
│   │   │   ├── WorkflowInspector/
│   │   │   ├── WorkspaceExplorer/
│   │   │   ├── BuildHistory/
│   │   │   └── LogsViewer/
│   │   ├── hooks/
│   │   ├── lib/
│   │   └── types/
│   ├── public/
│   ├── .env.local.example
│   ├── next.config.ts
│   └── package.json
├── docs/
│   ├── architecture.md
│   ├── board-profiles.md
│   └── validation-pipeline.md
├── scripts/
│   └── setup.sh
├── LICENSE
└── README.md
```

> **🔒 Locked modules:** `engine.py`, `session.py`, `retry.py`, and `subprocess_mgr.py` form the stable runtime core. They are not open for modification — all extension points are in `services/`.

---

## Testing

PromptForge has a layered suite with more than **1,700 backend tests**, plus frontend type checks and Electron tests. Exact counts are recorded in phase result documents.

```bash
cd backend
source .venv/bin/activate

# Run all tests
pytest

# Run by category
pytest tests/unit/
pytest tests/integration/
pytest tests/api/
pytest tests/workflow/

# Run with coverage report
pytest --cov=. --cov-report=html

# Run a specific module
pytest tests/unit/test_failure_classifier.py -v
```

### Test Summary

| Category | Count | Description |
|---|---|---|
| Unit | ~640 | Pure function and service-level tests |
| Integration | ~210 | Cross-service and pipeline tests |
| API | ~130 | FastAPI endpoint and WebSocket tests |
| Workflow | ~116 | End-to-end pipeline execution tests |
| **Total** | **1,700+** | Backend, frontend, Electron, and safety verification |

CI is configured to run the full suite on every push and pull request.

---

## Roadmap

### V1.0 — Release Candidate *(current)*

- [x] Multi-stage execution pipeline (Plan → Generate → Build → Flash → Monitor)
- [x] Validation pipeline (safety, constraint, firmware, syntax)
- [x] Wokwi simulation integration
- [x] Workspace and artifact management
- [x] WebSocket streaming
- [x] Monaco editor
- [x] Board detection and validation
- [x] 1,700+ backend tests maintained

### V2.0 — EDE Evolution *(planned)*

| Feature | Description | Status |
|---|---|---|
| **Electron shell** | Native desktop app; removes browser dependency, enables direct USB/serial access | 🔜 Planned |
| **AI Copilot** | Inline AI assistant inside the editor for code explanation, refactoring, and debugging suggestions | 🔜 Planned |
| **Device Manager** | Persistent device registry with port profiles, flash history, and health monitoring | 🔜 Planned |
| **Advanced Simulation** | Peripheral simulation (I2C, SPI, UART devices), logic analyser overlay, timing diagrams | 🔜 Planned |
| **Multi-board workflows** | Orchestrate concurrent firmware builds and flash operations across multiple boards | 🔜 Planned |
| **Plugin API** | Public extension API for custom board profiles, validators, and code-gen hooks | 🔜 Planned |
| **Offline mode** | Local LLM support via Ollama for air-gapped development environments | 🔜 Planned |

---

## Security Considerations

PromptForge executes compiler toolchains and flashes firmware to connected hardware. Read the following before deployment.

**LLM output validation**
All generated firmware passes the multi-layer validation pipeline before any build is attempted. Safety checks block code that attempts direct memory manipulation outside the allowed peripheral address ranges.

**Subprocess execution**
PlatformIO and serial processes are managed by `subprocess_mgr.py` with explicit allowlists, timeout enforcement, and output size limits.

**API keys**
Never commit `.env` files. Rotate your `OPENROUTER_API_KEY` if it is accidentally exposed.

**Local use**
PromptForge is designed for local or private-network deployment. The FastAPI server does not implement authentication by default. Do not expose port 8000 to the public internet without adding an authentication layer.

**Hardware safety**
PromptForge cannot guarantee that generated firmware is safe for all hardware configurations. Always review generated code before flashing to production or safety-critical devices.

---

## Contributing

Contributions are welcome. Please read the guidelines below before opening a pull request.

### Development Workflow

```bash
# Fork and clone
git clone https://github.com/your-username/promptforge.git
cd promptforge

# Create a feature branch
git checkout -b feat/your-feature-name

# Make your changes and run tests
cd backend && pytest

# Commit using Conventional Commits
git commit -m "feat(validator): add peripheral address range check"

# Push and open a PR against main
git push origin feat/your-feature-name
```

### Contribution Guidelines

- **Do not modify locked core modules** (`engine.py`, `session.py`, `retry.py`, `subprocess_mgr.py`). Open an issue first if you believe a change to core is necessary.
- All new code must be covered by tests. PRs that reduce overall coverage will not be merged.
- Follow existing code style. The backend uses `ruff` for linting; the frontend uses ESLint and Prettier.
- Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/).
- For new board profiles, add a JSON file under `backend/boards/` and include tests in `tests/unit/test_board_profiles.py`.

### Reporting Issues

Use the GitHub issue tracker. Include:

- PromptForge version
- OS and Python version
- Board and framework (if firmware-related)
- Full error output or logs from the Workflow Inspector

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for full text.

---

<div align="center">

**PromptForge** — built for engineers who move fast and care about correctness.

<br/>

<sub>
  Use the configured repository issue tracker for bugs and feature requests.
</sub>

<br/><br/>

<sub>© 2025 PromptForge Contributors · MIT License</sub>

</div>
