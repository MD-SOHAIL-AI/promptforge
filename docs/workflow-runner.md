# WorkflowRunner integration

## Architecture

`WorkflowRunner` is the production composition boundary for:

```text
Task -> Planner -> ExecutionPlan -> Coordinator -> ExecutionOutcome
```

The runner performs dependency orchestration only. It sends `Task.prompt` to
the planner, binds the resulting plan to the canonical `Task.task_id`, creates
or validates an immutable `ExecutionContext`, resolves context-aware tool
invocations, and delegates ordered execution to the coordinator.

Planning rules remain in `backend.agent.planner`. Step ordering, tool dispatch,
failure aggregation, and cancellation outcomes remain in
`backend.agent.coordinator`. Tool, generation, flashing, and monitoring
implementations remain in their existing modules.

## Execution context

When no context is supplied, the runner creates one from the task and plan:

- `task_id` comes from the canonical `Task`.
- `target_board` and `framework` come from the plan.
- `simulation_enabled` reflects `START_SIMULATION` in the plan.
- task provenance and caller metadata are namespaced under `metadata.task`.

An existing context may be supplied for enriched runtime state. Its `task_id`
must match the submitted task. The context is passed to the configured
`invocation_factory`, which is the binding point for adapters to create
`ToolInvocation` values. The runner never interprets tool arguments itself.

## Integration notes

- The runner accepts synchronous or asynchronous planner and factory results.
- Per-call invocation bindings override factory bindings for the same step.
- Planner and factory exceptions propagate to the caller; they are not
  misrepresented as execution failures.
- Coordinator failures remain represented by its terminal `ExecutionOutcome`.
- The runner is stateless across calls and can be reused by concurrent tasks.
- Existing coordinator step handlers should continue to be assembled from the
  workflow adapters or service layer; the runner does not replace them.
- The canonical plan contract intentionally excludes planner-only fields such
  as `estimated_tools`.
