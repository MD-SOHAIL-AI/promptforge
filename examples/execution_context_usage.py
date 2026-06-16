"""Usage examples for the shared PromptForge execution context.

Run from the repository root with::

    python -m examples.execution_context_usage
"""

from __future__ import annotations

import json

from backend.contracts.execution_context import ExecutionContext


def create_initial_context() -> ExecutionContext:
    """Create a context before board detection or firmware build."""

    return ExecutionContext(
        task_id="task-01JX6R4J7S9M8T2K3N4P5Q6R7S",
        project_path="workspace/projects/weather-station",
        target_board="ESP32",
        framework="PlatformIO",
        simulation_enabled=False,
        metadata={"requested_by": "user-42"},
    )


def create_enriched_context() -> ExecutionContext:
    """Create a new immutable snapshot after external stages produce data."""

    return ExecutionContext(
        task_id="task-01JX6R4J7S9M8T2K3N4P5Q6R7S",
        project_path="workspace/projects/weather-station",
        target_board="ESP32",
        framework="PlatformIO",
        firmware_path="workspace/builds/esp32dev/firmware.bin",
        build_artifact={
            "path": "workspace/builds/esp32dev/firmware.bin",
            "environment": "esp32dev",
            "artifact_type": "bin",
            "size_bytes": 248192,
        },
        board_info={
            "board_type": "ESP32",
            "port": "COM7",
            "vid": 0x10C4,
            "pid": 0xEA60,
            "serial_number": "LAB-ESP32-04",
        },
        metadata={"requested_by": "user-42", "build_attempt": 1},
    )


def serialize_and_restore(context: ExecutionContext) -> ExecutionContext:
    """Demonstrate a JSON transport or persistence round-trip."""

    payload = json.dumps(context.to_dict())
    return ExecutionContext.from_dict(json.loads(payload))


if __name__ == "__main__":
    initial = create_initial_context()
    enriched = create_enriched_context()

    print(json.dumps(initial.to_dict(), indent=2))
    print(json.dumps(enriched.to_dict(), indent=2))
    assert serialize_and_restore(enriched) == enriched
