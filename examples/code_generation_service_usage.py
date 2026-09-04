"""Code-generation service usage with an injected LLM provider.

Set ``OPENAI_API_KEY`` and run from the repository root with::

    python -m examples.code_generation_service_usage

The example returns an in-memory project. It does not write or build files.
"""

from __future__ import annotations

import asyncio
import json
import os

from backend.agent.planner import Planner
from backend.contracts.execution_context import ExecutionContext
from backend.services.code_generation_service import (
    CodeGenerationRequest,
    CodeGenerationService,
)
from backend.services.llm_service import OpenAIService


async def main() -> None:
    plan = Planner().plan("Build an ESP32 LED blinker using PlatformIO")
    context = ExecutionContext(
        task_id=plan.task_id,
        project_path="workspace/projects/esp32-blinker",
        target_board=plan.target_board.value,
        framework=plan.framework.value,
        metadata={"led_pin": 2},
    )

    async with OpenAIService(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
    ) as llm:
        generator = CodeGenerationService(llm)
        project = await generator.generate_project(
            CodeGenerationRequest(plan=plan, context=context)
        )

    print(project.list_files())
    print(project.get_file("src/main.cpp"))
    print(json.dumps(project.to_dict(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
