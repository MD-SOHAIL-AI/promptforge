from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.tools import tool_registry
from backend.tools.tool_registry import (
    DuplicateToolError,
    InvalidToolError,
    ToolNotFoundError,
    execute_tool,
    get_tool,
    list_tools,
    register_tool,
)


def test_builtin_tools_are_registered() -> None:
    assert list_tools() == [
        "board_detector",
        "build_firmware",
        "flash_firmware",
        "serial_monitor",
        "wokwi_simulator",
    ]
    assert callable(get_tool("build_firmware"))
    assert callable(get_tool("board_detector"))
    assert callable(get_tool("flash_firmware"))
    assert callable(get_tool("serial_monitor"))
    assert callable(get_tool("wokwi_simulator"))


def test_names_are_trimmed_for_registration_and_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_registry, "_TOOLS", dict(tool_registry._TOOLS))
    handler = lambda: "ok"
    register_tool("  custom  ", handler)

    assert get_tool(" custom ") is handler
    assert "custom" in list_tools()


@pytest.mark.parametrize("name", ["", "   ", "bad\x00name", None, 123])
def test_invalid_names_are_rejected(name: Any) -> None:
    with pytest.raises(InvalidToolError):
        register_tool(name, lambda: None)

    with pytest.raises(InvalidToolError):
        get_tool(name)


def test_non_callable_and_invalid_replace_flag_are_rejected() -> None:
    with pytest.raises(InvalidToolError, match="callable"):
        register_tool("invalid_handler", object())  # type: ignore[arg-type]

    with pytest.raises(InvalidToolError, match="replace"):
        register_tool(
            "invalid_replace",
            lambda: None,
            replace=1,  # type: ignore[arg-type]
        )


def test_duplicate_registration_is_rejected() -> None:
    with pytest.raises(DuplicateToolError, match="already registered"):
        register_tool("build_firmware", lambda: None)


def test_registration_can_explicitly_replace_a_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replacement = lambda: "replacement"
    monkeypatch.setitem(tool_registry._TOOLS, "board_detector", replacement)

    registered = register_tool("board_detector", replacement, replace=True)

    assert registered is replacement
    assert get_tool("board_detector") is replacement


def test_unknown_tool_raises_clear_error() -> None:
    with pytest.raises(ToolNotFoundError, match="missing_tool"):
        get_tool("missing_tool")

    with pytest.raises(ToolNotFoundError, match="missing_tool"):
        asyncio.run(execute_tool("missing_tool"))


def test_execute_tool_forwards_arguments_to_sync_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def add(left: int, right: int, *, offset: int = 0) -> int:
        return left + right + offset

    monkeypatch.setitem(tool_registry._TOOLS, "add", add)

    assert asyncio.run(execute_tool("add", 2, 3, offset=4)) == 9


def test_execute_tool_awaits_async_callable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def multiply(left: int, right: int) -> int:
        await asyncio.sleep(0)
        return left * right

    monkeypatch.setitem(tool_registry._TOOLS, "multiply", multiply)

    assert asyncio.run(execute_tool("multiply", 6, 7)) == 42


def test_execute_tool_awaits_custom_awaitable_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def delayed_result() -> Any:
        async def result() -> str:
            return "done"

        return result()

    monkeypatch.setitem(tool_registry._TOOLS, "delayed", delayed_result)

    assert asyncio.run(execute_tool("delayed")) == "done"


def test_execute_tool_propagates_tool_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail() -> None:
        raise RuntimeError("tool failed")

    monkeypatch.setitem(tool_registry._TOOLS, "fail", fail)

    with pytest.raises(RuntimeError, match="tool failed"):
        asyncio.run(execute_tool("fail"))


def test_execute_tool_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancel() -> None:
        raise asyncio.CancelledError

    monkeypatch.setitem(tool_registry._TOOLS, "cancel", cancel)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(execute_tool("cancel"))


def test_board_detector_builtin_executes_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = [object()]
    monkeypatch.setattr(
        tool_registry.BoardDetector,
        "detect_boards",
        lambda self: expected,
    )

    assert asyncio.run(execute_tool("board_detector")) is expected


def test_build_and_flash_builtins_forward_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    flash_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def fake_build(*args: Any, **kwargs: Any) -> str:
        build_calls.append((args, kwargs))
        return "build-result"

    async def fake_flash(*args: Any, **kwargs: Any) -> str:
        flash_calls.append((args, kwargs))
        return "flash-result"

    monkeypatch.setitem(tool_registry._TOOLS, "build_firmware", fake_build)
    monkeypatch.setitem(tool_registry._TOOLS, "flash_firmware", fake_flash)

    assert asyncio.run(execute_tool("build_firmware", "config", "manager")) == (
        "build-result"
    )
    assert asyncio.run(
        execute_tool(
            "flash_firmware",
            "artifact",
            "board",
            "config",
            subprocess_mgr="manager",
        )
    ) == "flash-result"
    assert build_calls == [(("config", "manager"), {})]
    assert flash_calls == [
        (("artifact", "board", "config"), {"subprocess_mgr": "manager"})
    ]


def test_serial_monitor_builtin_constructs_and_observes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}

    class FakeMonitor:
        def __init__(self, config: Any, runtime: Any = None) -> None:
            calls["config"] = config
            calls["runtime"] = runtime

        async def observe(self, callback: Any = None) -> str:
            calls["callback"] = callback
            return "observe-result"

    monkeypatch.setattr(tool_registry, "SerialMonitor", FakeMonitor)
    callback = lambda observation: None

    result = asyncio.run(
        execute_tool("serial_monitor", "config", "runtime", callback)
    )

    assert result == "observe-result"
    assert calls == {
        "config": "config",
        "runtime": "runtime",
        "callback": callback,
    }
