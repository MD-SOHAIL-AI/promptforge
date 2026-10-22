"""Task-local subprocess ownership inherited by child tasks and worker threads."""
from contextvars import ContextVar

process_owner: ContextVar[str | None] = ContextVar("forgex_process_owner", default=None)


async def owned_operation(owner: str, operation):
    token = process_owner.set(owner)
    try:
        return await operation
    finally:
        process_owner.reset(token)
