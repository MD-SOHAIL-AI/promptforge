# ExecutionPlan contract migration

`backend.contracts.execution_plan` is now the canonical owner of
`ExecutionPlan`, `ExecutionStep`, and `TaskType`.

## Import changes

Replace planner-owned imports:

```python
from backend.agent.planner import ExecutionPlan, ExecutionStep, TaskType
```

with contract imports:

```python
from backend.contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    TaskType,
)
```

`backend.agent.planner` temporarily re-exports the canonical enums and a
compatible `ExecutionPlan` subclass so existing callers continue to work.
New code must use the contract module directly.

## Field changes

The canonical plan fields are:

- `task_id`
- `task_type`
- `target_board`
- `framework`
- `requirements`
- `execution_steps`
- `confidence`
- `metadata`

`estimated_tools` is not part of the canonical contract. Tool selection is a
coordinator concern and must be derived from `execution_steps` outside the
contract. The planner compatibility subclass still accepts and exposes
`estimated_tools`, but `to_dict()` intentionally omits it.

`target_board` and `framework` are serialized as strings. Planner-owned
string enums remain valid constructor inputs, but consumers must not require
planner enum identity at a contract boundary.

## Serialization

Use `plan.to_dict()` for API, persistence, and service handoffs. Reconstruct
plans with `ExecutionPlan.from_dict(data)`. The serialized schema is exact:
all canonical fields are required and unknown fields are rejected.

Metadata must be JSON-compatible. Mutable mappings and sequences are copied
and recursively frozen on construction; `to_dict()` returns fresh mutable
JSON-compatible containers.

## Removal sequence

1. Move consumer imports to `backend.contracts.execution_plan`.
2. Remove reads and constructor arguments for `estimated_tools`.
3. Replace planner enum identity checks at boundaries with string values or
   local enum parsing.
4. Remove the compatibility `ExecutionPlan` subclass from `planner.py` after
   all callers have migrated.
