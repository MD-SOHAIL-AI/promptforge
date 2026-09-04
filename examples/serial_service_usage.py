"""Unified serial service usage.

Set ``PROMPTFORGE_SERIAL_PORT`` and run from the repository root with::

    python -m examples.serial_service_usage
"""

from __future__ import annotations

import asyncio
import os

from backend.services.serial_service import SerialConfiguration, SerialService


async def main() -> None:
    service = SerialService(
        SerialConfiguration(
            port=os.environ["PROMPTFORGE_SERIAL_PORT"],
            baudrate=int(os.environ.get("PROMPTFORGE_SERIAL_BAUD", "115200")),
            timeout_s=1.0,
        )
    )

    connection = await service.connect()
    print(connection.to_dict())
    try:
        await service.write("status\n")
        response = await service.read_until("\n", max_bytes=4096)
        print(response.decode("utf-8", errors="replace").rstrip())
    finally:
        print((await service.disconnect()).to_dict())


if __name__ == "__main__":
    asyncio.run(main())
