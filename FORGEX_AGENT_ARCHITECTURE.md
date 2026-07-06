# ForgeX Autonomous Embedded Agent Architecture

Generated: 2026-06-17

## Safety Rule

No agent may flash, erase, reset, OTA-upload, debug-attach, or otherwise affect hardware without explicit user approval recorded by the approval service.

## Agent Set

| Agent | Responsibility | Existing V1 Reuse |
| --- | --- | --- |
| Planner Agent | Converts user goal into task graph, constraints, acceptance criteria. | `Planner`, `WorkflowRunner` |
| Hardware Agent | Identifies target board/device, capabilities, pins, risks. | `BoardDetector`, board validators, constraint engine |
| Firmware Agent | Creates/edits firmware and project files. | `CodeGenerationService`, `GenerateCodeHandler`, `ProjectService` |
| Build Agent | Builds firmware and parses failures. | `PlatformIOService`, `build_firmware.py`, failure classifier |
| Debug Agent | Diagnoses build/flash/runtime failures and proposes fixes. | `Debugger`, `failure_classifier` |
| Flash Agent | Prepares flash plan and requests approval before action. | `flash_firmware.py`, `FlashAdapter` |
| Test Agent | Runs unit/simulation/HIL tests and validates acceptance criteria. | new; can reuse subprocess/runtime |
| Serial Monitor Agent | Starts monitor, reads logs, detects expected behavior/failures. | `SerialService`, `serial_monitor.py` |
| Documentation Agent | Produces README, wiring, board notes, usage docs. | new; reuse project files/artifacts |
| Knowledge Agent | Maintains board/project/toolchain memory and retrieval. | new; reuse persistence/events |

## Workflow

```text
User Goal
  -> Planner Agent
  -> Task Graph
  -> Agent Scheduler
  -> Tool Execution
  -> Validation
  -> Human Approval Request
  -> Hardware Action
  -> Observation
  -> Fix/Rerun Loop
  -> Final Report
```

## Task Graph Model

Each task node:

```text
node_id
session_id
agent_type
goal
inputs
required_tools
dependencies
status
risk_level
requires_approval
artifacts
events
result
failure
```

Example:

```text
N1 Planner: produce plan
N2 Hardware: detect/select ESP32
N3 Firmware: create PlatformIO project
N4 Build: compile
N5 Debug: fix build errors if any
N6 Test: simulate or run software tests
N7 Flash: request approval
N8 Flash: execute only after approval
N9 Monitor: observe serial logs
N10 Docs: generate wiring and run notes
```

## Agent Runtime

Core services:

- `AgentSessionService`: creates sessions, owns state.
- `TaskGraphService`: stores graph and transitions.
- `AgentScheduler`: dispatches ready nodes.
- `ToolPermissionService`: classifies tool risk.
- `ApprovalService`: user approval records.
- `EventStore`: durable event stream.
- `ArtifactService`: stores generated plans, diffs, firmware, logs.
- `ModelRouter`: task-based model choice.
- `KnowledgeStore`: retrieval and memory.

## Tool Permission Classes

| Class | Examples | Policy |
| --- | --- | --- |
| Read-only | list files, read project, list boards | allowed with session scope |
| Write workspace | edit generated project, write docs | allowed if workspace-scoped |
| Build/simulate | PlatformIO build, Wokwi | allowed after plan confirmation |
| Observe hardware | list ports, serial monitor | allowed with device selection |
| Mutate hardware | flash, erase, OTA, reset, debug attach | explicit user approval |
| Dangerous system | arbitrary shell, delete outside workspace | deny or explicit advanced approval |

## Agent Communication

Agents do not call each other directly. They exchange structured artifacts through the task graph and event store:

- Plans.
- Constraints.
- Generated diffs.
- Build reports.
- Flash manifests.
- Serial observations.
- Debug recommendations.
- Documentation artifacts.

## Human Approval Flow

```text
Flash Agent creates FlashApprovalRequest
  -> includes board, port, firmware artifact hash, command, risk notes
  -> UI shows request
  -> user approves or denies
  -> approval token stored
  -> Flash tool checks token immediately before execution
  -> token consumed
```

Approval must be one-time by default. Any change in artifact hash, port, board, or command invalidates the approval.

## Validation Gates

Before build:

- Project schema validation.
- Firmware validation.
- Safety validation.
- Constraint validation.

Before flash:

- Successful build artifact.
- Artifact hash recorded.
- Board/device match.
- BoardValidator pass.
- Explicit user approval.

After flash:

- Serial monitor observations.
- Expected boot/telemetry detection.
- Failure classification and debug loop.

## V1 Integration Points

Do not replace:

- `WorkflowRunner`
- `Coordinator`
- `GenerateCodeHandler`
- `CodeGenerationService`
- `ProjectService`
- `PlatformIOService`
- `SubprocessManager`
- `BoardDetector`
- validators/tools/runtime results

Wrap them in agent tool nodes and add approval/event/session persistence.

