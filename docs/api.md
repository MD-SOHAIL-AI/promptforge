# PromptForge API

The FastAPI layer is a transport adapter over the existing PromptForge
workflow, services, contracts, and tools. It does not own planning, code
generation, builds, flashing, serial I/O, or project persistence.

Run the application from the repository root:

```powershell
uvicorn backend.api.app:app --host 0.0.0.0 --port 8000 --env-file .env
```

Interactive OpenAPI documentation is available at `/docs`; the schema is at
`/openapi.json`.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `PROMPTFORGE_VERSION` | Version returned by `/health` | `0.1.0` |
| `PROMPTFORGE_PROJECTS_ROOT` | Managed project directory | `.promptforge/projects` |
| `PROMPTFORGE_LLM_PROVIDER` | `OPENAI`, `OPENROUTER`, `ANTHROPIC`, or `GEMINI` | Required |
| `OPENROUTER_API_KEY` | OpenRouter bearer credential when the provider is `OPENROUTER` | Required |
| `PROMPTFORGE_MODEL` | Provider model identifier, such as `openai/gpt-4.1-mini` | Required |
| `PROMPTFORGE_LLM_TIMEOUT_SECONDS` | LLM HTTP and generation deadline in seconds | `180` |

For OpenRouter, create `.env` from `.env.example` and supply a real key:

```dotenv
PROMPTFORGE_LLM_PROVIDER=OPENROUTER
OPENROUTER_API_KEY=replace-with-your-openrouter-key
PROMPTFORGE_MODEL=openai/gpt-4.1-mini
PROMPTFORGE_LLM_TIMEOUT_SECONDS=180
```

PromptForge sends generation requests to OpenRouter's OpenAI-compatible
`POST https://openrouter.ai/api/v1/chat/completions` endpoint. The API key is
read only from the process environment and sent as a bearer token.

## Route Ownership

- `POST /execute` calls the canonical `execute_prompt()` composition and
  returns its terminal `ExecutionOutcome`.
- `/projects` delegates persistence operations to `ProjectService`.
- `POST /build` delegates to `PlatformIOService.build()`.
- `POST /flash` builds through `PlatformIOService`, validates a detected
  board, and invokes the registered `flash_firmware` tool.
- `/monitor/*` delegates connection lifecycle to `SerialService`.
- `/ws/execution/{task_id}` streams API lifecycle observations. It is bounded,
  process-local transport state, not a workflow engine or event bus.

## Execute

```http
POST /execute
Content-Type: application/json

{
  "prompt": "Blink LED on ESP32",
  "task_id": "task-client-001"
}
```

`task_id` is optional. Clients requiring live progress should create a unique
safe ID, connect the WebSocket first, and submit the same ID to `/execute`.

```json
{
  "status": "COMPLETED",
  "task_id": "task-client-001",
  "execution_time_ms": 1234,
  "steps": [],
  "failures": []
}
```

Workflow failures are represented in this typed terminal response. HTTP error
statuses are reserved for malformed input, missing resources, conflicts, or an
operation that could not be invoked.

To prove firmware generation through OpenRouter, start the API with the
OpenRouter environment above and submit:

```powershell
$body = @{
    prompt = "Generate PlatformIO firmware for an ESP32 that blinks the onboard LED every 500 ms"
    task_id = "task-openrouter-blink-001"
} | ConvertTo-Json

Invoke-RestMethod `
    -Method Post `
    -Uri http://localhost:8000/execute `
    -ContentType application/json `
    -Body $body
```

The response's `GENERATE_CODE` step must report success and its result must
identify the persisted generated project. Build, flash, and monitor results
also depend on the local PlatformIO installation and connected hardware.

## Projects, Build, And Flash

```http
GET /projects
GET /projects/project-123
DELETE /projects/project-123
```

```http
POST /build
Content-Type: application/json

{"project_id": "project-123", "environment": "esp32dev"}
```

```http
POST /flash
Content-Type: application/json

{
  "project_id": "project-123",
  "board_type": "ESP32",
  "port": "COM7",
  "environment": "esp32dev",
  "baudrate": 115200,
  "verify": true,
  "timeout_s": 60
}
```

Project and firmware filesystem paths are never accepted from clients.

## Monitor

```http
POST /monitor/start
Content-Type: application/json

{"port": "COM7", "baudrate": 115200, "timeout_s": 1.0}
```

Use `GET /monitor/status` for the current connection snapshot and
`POST /monitor/stop` for idempotent shutdown.

## WebSocket Progress

Connect to:

```text
ws://localhost:8000/ws/execution/task-client-001
```

Reconnect with `?after=<sequence>` to replay only newer retained events.
Events contain a sequence, UTC timestamp, task ID, execution ID, workflow
correlation ID, event type, and structured payload. Event types are:

```text
TASK_CREATED
PLAN_GENERATED
CODE_GENERATION_STARTED
CODE_GENERATION_COMPLETED
BUILD_STARTED
BUILD_COMPLETED
FLASH_STARTED
FLASH_COMPLETED
MONITOR_STARTED
WORKFLOW_COMPLETED
```

Streams and subscriber queues are bounded. Completed history expires after a
retention period. Multi-worker deployments must route a task and its WebSocket
subscribers to the same worker.

## Errors And Correlation

All HTTP errors use one shape:

```json
{
  "code": "PROJECT_NOT_FOUND",
  "message": "Project was not found",
  "details": {"project_id": "project-123"}
}
```

Clients may send `X-Request-ID`; otherwise the API generates one. Responses
include `X-Request-ID`, and execution responses additionally include
`X-Execution-ID` and `X-Workflow-Correlation-ID`. Logs are emitted as JSON and
include these identifiers without returning raw exceptions to clients.
