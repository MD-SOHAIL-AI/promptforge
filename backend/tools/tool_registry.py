
"""Central registry for executable PromptForge tools.

The registry is intentionally process-local and lightweight. It maps stable
tool names to callables, performs no orchestration or dependency injection,
and leaves tool-specific validation and result construction to each tool.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias, TypeVar, cast

from ..runtime.result import ObserveResult
from .board_detector import BoardDetector, BoardInfo
from .build_firmware import build_firmware
from .flash_firmware import flash_firmware
from .serial_monitor import (
    ObservationCallback,
    SerialMonitor,
    SerialMonitorConfig,
)
from .wokwi_simulator import simulate_firmware

__all__ = [
    "DuplicateToolError",
    "InvalidToolError",
    "ToolNotFoundError",
    "ToolRegistryError",
    "execute_tool",
    "get_tool",
    "list_tools",
    "register_tool",
]

ToolCallable: TypeAlias = Callable[..., Any]
_T = TypeVar("_T", bound=ToolCallable)


class ToolRegistryError(RuntimeError):
    """Base exception for registry operations."""


class InvalidToolError(ToolRegistryError, ValueError):
    """A tool name or handler is invalid."""


class DuplicateToolError(ToolRegistryError):
    """A tool name is already registered."""


class ToolNotFoundError(ToolRegistryError, LookupError):
    """No tool is registered under the requested name."""


_TOOLS: dict[str, ToolCallable] = {}


def register_tool(
    name: str,
    tool: _T,
    *,
    replace: bool = False,
) -> _T:
    """Register ``tool`` under ``name`` and return the registered callable.

    Duplicate names are rejected unless ``replace`` is explicitly enabled.
    Returning the callable keeps registration convenient for module-level
    setup while preserving the original callable's type for callers.
    """
    normalized_name = _normalize_name(name)
    if not callable(tool):
        raise InvalidToolError("tool must be callable")
    if not isinstance(replace, bool):
        raise InvalidToolError("replace must be a boolean")
    if normalized_name in _TOOLS and not replace:
        raise DuplicateToolError(
            f"tool {normalized_name!r} is already registered"
        )

    _TOOLS[normalized_name] = tool
    return tool


def get_tool(name: str) -> ToolCallable:
    """Return the callable registered under ``name``."""
    normalized_name = _normalize_name(name)
    try:
        return _TOOLS[normalized_name]
    except KeyError as exc:
        raise ToolNotFoundError(
            f"tool {normalized_name!r} is not registered"
        ) from exc


def list_tools() -> list[str]:
    """Return registered tool names in deterministic order."""
    return sorted(_TOOLS)


async def execute_tool(name: str, *args: Any, **kwargs: Any) -> Any:
    """Execute a registered tool and await its result when necessary.

    Arguments are forwarded unchanged. Tool exceptions, including external
    cancellation, are deliberately allowed to propagate to the caller.
    """
    result = get_tool(name)(*args, **kwargs)
    if inspect.isawaitable(result):
        return await cast(Awaitable[Any], result)
    return result


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        raise InvalidToolError("tool name must be a string")
    normalized_name = name.strip()
    if not normalized_name:
        raise InvalidToolError("tool name must be non-empty")
    if "\x00" in normalized_name:
        raise InvalidToolError("tool name cannot contain NUL characters")
    return normalized_name


def _detect_boards() -> list[BoardInfo]:
    return BoardDetector().detect_boards()


async def _monitor_serial(
    config: SerialMonitorConfig,
    runtime: Any = None,
    on_observation: ObservationCallback | None = None,
) -> ObserveResult:
    return await SerialMonitor(config, runtime=runtime).observe(on_observation)


def register_defaults() -> None:
    """Register the built-in PromptForge tool callables."""
    # fix: defer default tool registration until application startup.
    register_tool("build_firmware", build_firmware)
    register_tool("board_detector", _detect_boards)
    register_tool("flash_firmware", flash_firmware)
    register_tool("serial_monitor", _monitor_serial)
    register_tool("wokwi_simulator", simulate_firmware)
