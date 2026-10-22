"""Construct, inspect, and serialize a generated firmware project.

Run from the repository root with::

    python -m examples.generated_project_usage
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from backend.contracts.generated_project import GeneratedFile, GeneratedProject


def main() -> None:
    project = GeneratedProject(
        project_id="project-example-001",
        project_name="esp32-blinker",
        target_board="ESP32",
        framework="PlatformIO",
        files=(
            GeneratedFile(
                path="platformio.ini",
                content="[env:esp32dev]\nboard = esp32dev\n",
                file_type="ini",
            ),
            GeneratedFile(
                path="src/main.cpp",
                content="void setup() {}\nvoid loop() {}\n",
                file_type="cpp",
            ),
        ),
        created_at=datetime.now(timezone.utc),
        metadata={"task_id": "task-example-001"},
    )

    print(project.list_files())
    print(project.get_file("src/main.cpp"))
    print(json.dumps(project.to_dict(), indent=2))


if __name__ == "__main__":
    main()
