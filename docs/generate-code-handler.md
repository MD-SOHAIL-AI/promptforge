# GenerateCodeHandler

## Architecture

`GenerateCodeHandler` is the production implementation of the coordinator's
`GENERATE_CODE` step:

```text
ExecutionPlan
  -> CodeGenerationRequest
  -> CodeGenerationService
  -> GeneratedProject
  -> ProjectService
  -> updated ExecutionContext
  -> GenerateCodeResult
```

It composes existing architecture only. It does not plan, build, flash,
monitor, retry, invoke tools, or mutate runtime/session state.

## Ownership boundaries

- `Planner` owns intent interpretation and execution-plan construction.
- `CodeGenerationService` owns source generation and framework validation.
- `ProjectService` owns filesystem persistence and project ownership rules.
- `GenerationAdapter` owns the immutable context conversion.
- `Coordinator` owns step ordering and failure aggregation.
- `WorkflowRunner` owns task-to-plan-to-coordinator composition.
- `GenerateCodeHandler` owns only the generate, validate, persist handoff.

## Context integration

The handler receives an immutable `ExecutionContext`. On success it creates a
new context containing the persisted project path, generated project identity,
target board, framework, and generation summary. The updated context is stored
as canonical serialized data in `GenerateCodeResult.metadata` and exposed by
the `execution_context` property.

Downstream coordinator invocation resolvers read the previous `StepResult`,
obtain `GenerateCodeResult.execution_context`, and construct build-stage inputs
without mutable shared state.

## WorkflowRunner and Coordinator

Register the handler under `ExecutionStep.GENERATE_CODE` when constructing the
existing `Coordinator`. Supply the same initial context to `WorkflowRunner`.
The runner invokes the planner and coordinator normally; no runner changes are
required.

`GenerateCodeResult.success` and `message` are compatibility properties for
the current coordinator. Non-success statuses become structured coordinator
failures with categories `VALIDATION_FAILED`, `GENERATION_FAILED`, or
`PERSISTENCE_FAILED`.

## Failure handling

Operational exceptions are converted into immutable failure results:

- invalid plan, request, generated project, or context update:
  `VALIDATION_FAILED`
- generation provider or output extraction failure: `GENERATION_FAILED`
- project persistence or invalid persistence response: `PERSISTENCE_FAILED`

Failure metadata records the phase, exception type, message, and native
`CodeGenerationError` details when available. Raw operational exceptions do
not escape. `asyncio.CancelledError` remains control flow and is converted by
the existing coordinator into its terminal `CANCELLED` outcome.

## Observability

Structured key-value log messages cover generation start/completion,
persistence start/completion, workflow completion, and classified validation
or operational failures. Logs contain task and project identifiers but no
generated source content.
