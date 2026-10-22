"""Provider-neutral LLM service usage examples.

Set ``OPENAI_API_KEY`` and run from the repository root with::

    python -m examples.llm_service_usage
"""

from __future__ import annotations

import asyncio
import os

from backend.services.llm_service import LLMRequest, OpenAIService


async def generate_once() -> None:
    request = LLMRequest(
        prompt="Explain what a watchdog timer does in embedded firmware.",
        system_prompt="Answer concisely and technically.",
        temperature=0.2,
        max_tokens=300,
        metadata={"feature": "documentation"},
    )

    async with OpenAIService(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
    ) as service:
        if not await service.health_check():
            raise RuntimeError("OpenAI health check failed")

        response = await service.generate(request)
        print(response.content)
        print(response.to_dict())


async def stream_once() -> None:
    request = LLMRequest(
        prompt="List three causes of an ESP32 boot loop.",
        max_tokens=200,
    )

    async with OpenAIService(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
    ) as service:
        async for chunk in service.generate_stream(request):
            print(chunk, end="", flush=True)
        print()


if __name__ == "__main__":
    asyncio.run(generate_once())
    asyncio.run(stream_once())
