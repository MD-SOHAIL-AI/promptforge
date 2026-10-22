"""Usage examples for the canonical PromptForge task contract.

Run from the repository root with::

    python -m examples.task_usage
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.contracts.task import Task, TaskPriority, TaskSource


def create_user_task() -> Task:
    """Create a user-originated task using the standard defaults."""

    return Task(
        task_id="task-01JX6R4J7S9M8T2K3N4P5Q6R7S",
        prompt="Build an ESP32 temperature monitor",
        metadata={"project_id": "weather-station"},
    )


def create_api_task() -> Task:
    """Create a high-priority task with an explicit timestamp."""

    return Task(
        task_id="task-01JX6R6E5PX0Q1W2E3R4T5Y6U7",
        prompt="Flash the latest approved firmware",
        source=TaskSource.API,
        priority=TaskPriority.HIGH,
        created_at=datetime(2026, 6, 10, 12, 30, tzinfo=timezone.utc),
        metadata={"board_id": "lab-esp32-04", "release": "v1.8.0"},
    )


def serialize_and_restore(task: Task) -> Task:
    """Demonstrate a JSON transport round-trip."""

    payload = json.dumps(task.to_dict())
    return Task.from_dict(json.loads(payload))


if __name__ == "__main__":
    user_task = create_user_task()
    api_task = create_api_task()

    print(json.dumps(user_task.to_dict(), indent=2))
    print(json.dumps(api_task.to_dict(), indent=2))
    assert serialize_and_restore(api_task) == api_task
