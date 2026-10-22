# Workflow adapters

## Architecture

The workflow adapters are pure boundaries between existing PromptForge types:

```text
GeneratedProject + materialized path -> BuildConfig
BuildResult                         -> BuildArtifact
BoardInfo + BuildArtifact          -> FlashConfig
BoardInfo                           -> SerialMonitorConfig
stage value + ExecutionContext      -> new ExecutionContext
```

They do not materialize projects, invoke services, execute tools, select a
workflow, or mutate context. Each adapter validates its source values, creates
the next existing architecture type, and returns a new immutable context
snapshot where appropriate.

`GenerationAdapter` requires the path produced by `ProjectService`, either as
an explicit argument, an existing context path, or `metadata.project_path`.
It parses the generated `platformio.ini` only to deterministically bind a
single environment and board into `BuildConfig`.

## Context updates

Context updates preserve existing fields and metadata. Stage summaries are
stored under `metadata.workflow.<stage>`:

- `generation`: project identity, name, and file count
- `build`: artifact identity and reproducibility data
- `flash`: port, flash policy, and artifact path
- `monitor`: port and serial observation policy

Artifact and board snapshots use the canonical `ExecutionContext` fields.
The original context remains unchanged.

## Validation strategy

- Validate concrete source types at every public boundary.
- Reject failed, cancelled, partial, or malformed build results.
- Require positive artifact sizes and consistent file extensions.
- Reject unknown boards, invalid USB identifiers, and empty ports.
- Require flash and monitor ports to match the detected board.
- Require generated project identity, board, and framework to agree with the
  execution context when those values are known.
- Validate configuration values through the existing `BuildConfig`,
  `FlashConfig`, and `SerialMonitorConfig` constructors.
- Perform no filesystem existence checks. Filesystem validation remains owned
  by project services and tools at execution time.

## Integration

Use each adapter after its producing service or tool returns and before the
next coordinator invocation is constructed. The `adapt(...)` methods return a
`(next_input, updated_context)` tuple. Function-level APIs are also exported
for callers that prefer explicit conversion and context-update steps.
