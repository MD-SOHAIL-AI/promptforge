"""Task-local output observer; subprocess ownership and cancellation stay unchanged."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Awaitable, Callable, Iterator

OutputObserver = Callable[[str], Awaitable[None]]
observer: ContextVar[OutputObserver | None] = ContextVar(
    "process_output_observer", default=None
)


@contextmanager
def observe_output(callback: OutputObserver) -> Iterator[None]:
    token = observer.set(callback)
    try:
        yield
    finally:
        observer.reset(token)
