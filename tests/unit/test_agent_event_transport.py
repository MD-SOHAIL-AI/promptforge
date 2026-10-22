"""Agent SSE responses must pass through the desktop proxy without buffering."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.api.routes import agent_runtime


@pytest.mark.parametrize("route", ["run", "session", "activity"])
def test_agent_streams_disable_proxy_compression(monkeypatch, route):
    event = {"sequence": 1, "run_id": "r", "event_type": "runtime.completed_with_warning"}
    session_store = SimpleNamespace(get=lambda _: SimpleNamespace(kind="agent"))
    event_store = SimpleNamespace(events=lambda *args, **kwargs: [event])
    service = SimpleNamespace(
        get_run=lambda _: SimpleNamespace(status="completed_with_warning"),
        events=lambda *args, **kwargs: [event] if not kwargs.get("after_sequence") else [],
        transient_events=lambda *args, **kwargs: [],
        orchestrator=None,
    )
    monkeypatch.setattr(agent_runtime, "_session_store", lambda _: session_store)
    monkeypatch.setattr(agent_runtime, "_service", lambda _: service)
    monkeypatch.setattr(agent_runtime, "_activity_broker", lambda _: SimpleNamespace(recent=event_store.events))
    monkeypatch.setattr(agent_runtime, "services", lambda _: SimpleNamespace(
        agent_orchestrator=object(), agent_event_store=event_store,
    ))
    monkeypatch.setattr(agent_runtime, "emit_diagnostic_event", lambda **_: None)

    async def disconnected():
        return False

    request = SimpleNamespace(headers={}, is_disconnected=disconnected)

    async def check():
        endpoint = {
            "run": agent_runtime.agent_runtime_events,
            "session": agent_runtime.agent_session_events,
            "activity": agent_runtime.agent_session_activity_events,
        }[route]
        response = await endpoint("r" if route == "run" else "s", request, 0)
        assert "no-transform" in response.headers["cache-control"]
        assert "no-cache" in response.headers["cache-control"]
        assert response.headers["x-accel-buffering"] == "no"
        assert "content-encoding" not in response.headers
        iterator = response.body_iterator
        assert "runtime.completed_with_warning" in await anext(iterator)
        if route == "run":
            # Warning completion is terminal; the run stream must close.
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(iterator), timeout=1)
        else:
            await iterator.aclose()

    asyncio.run(check())
