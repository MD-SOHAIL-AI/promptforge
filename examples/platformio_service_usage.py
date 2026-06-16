"""Unified PlatformIO service usage.

Run from the repository root after setting ``PLATFORMIO_PROJECT_DIR``::

    python -m examples.platformio_service_usage
"""

from __future__ import annotations

import asyncio
import os

from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.platformio_service import PlatformIOService


async def main() -> None:
    project_path = os.environ["PLATFORMIO_PROJECT_DIR"]
    manager = SubprocessManager(max_concurrent=2)
    platformio = PlatformIOService(manager, timeout_s=180)

    project = platformio.validate_project(project_path)
    for environment in project.environments:
        print(environment.to_dict())

    dependency_result = await platformio.install_dependencies(project_path)
    if not dependency_result.success:
        raise RuntimeError(dependency_result.stderr_text)

    build_result = await platformio.build(project_path)
    print(build_result.api_dict())

    boards = await platformio.list_boards("esp32", installed_only=True)
    print(f"Installed ESP32 boards: {len(boards)}")


if __name__ == "__main__":
    asyncio.run(main())
