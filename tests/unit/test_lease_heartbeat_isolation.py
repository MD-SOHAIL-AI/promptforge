"""The session lease must keep renewing while the application loop is busy."""
import asyncio
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.product_agent_service import ProductAgentRun
from tests.unit.test_agent_orchestrator_v2 import _changes, _providers


def test_lease_renews_while_the_event_loop_is_blocked(tmp_path):
    async def scenario():
        renewed_during_block = threading.Event()
        first_renewal = threading.Event()
        blocked = threading.Event()
        store = AgentOrchestrationStore(tmp_path / "lease.sqlite3")
        renew = store.renew_lease
        renewals = 0
        def tracking(grant, **kwargs):
            nonlocal renewals
            value = renew(grant, **kwargs)
            renewals += 1
            if renewals == 1:
                first_renewal.set()
            if blocked.is_set():
                renewed_during_block.set()
            return value
        store.renew_lease = tracking
        observed = []
        async def execute(request):
            assert await asyncio.to_thread(first_renewal.wait, 5)
            # Deliberately block the event loop, as a slow synchronous OS call
            # can do. A loop-owned timer cannot run until this wait returns.
            blocked.set()
            observed.append(renewed_during_block.wait(2))
            return {"success": False, "classification": "EXPECTED_TEST_STOP"}
        orchestrator = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
            store=store, run_factory=ProductAgentRun, autonomous_executor=execute,
            lease_heartbeat_interval_seconds=.01)
        workspace = tmp_path / "project"
        workspace.mkdir()
        try:
            run = await orchestrator.start_run(project_id="project", active_workspace_root=workspace,
                instruction="Repair and build", provider_id="verified_template", autonomy="build_only",
                session_id="lease-isolation")
            await orchestrator._tasks[run.run_id]
            assert observed == [True]
            assert orchestrator.get_run(run.run_id).classification == "EXPECTED_TEST_STOP"
        finally:
            await orchestrator.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("renew", [False, True])
def test_lease_clock_is_checked_after_waiting_for_the_write_transaction(tmp_path, monkeypatch, renew):
    import backend.agent_runtime.orchestration_store as module
    class Clock(datetime):
        value = datetime.now(timezone.utc)
        @classmethod
        def now(cls, tz=None):
            return cls.value
    monkeypatch.setattr(module, "datetime", Clock)
    store = AgentOrchestrationStore(tmp_path / "clock.sqlite3")
    grant = store.acquire_lease("session", "owner", ttl_seconds=5) if renew else None
    transaction = store._transaction
    @contextmanager
    def waited_transaction():
        with transaction() as connection:
            Clock.value += timedelta(seconds=6)
            yield connection
    monkeypatch.setattr(store, "_transaction", waited_transaction)
    if renew:
        with pytest.raises(ValueError, match="AGENT_SESSION_LEASE_EXPIRED"):
            store.renew_lease(grant, ttl_seconds=5)
    else:
        granted = store.acquire_lease("session", "owner", ttl_seconds=5)
        expiry = datetime.fromisoformat(granted.expires_at.replace("Z", "+00:00"))
        assert expiry >= Clock.value + timedelta(seconds=5)


def test_lease_loss_fences_tools_even_before_the_event_loop_can_handle_it(tmp_path):
    async def scenario():
        first_renewal = threading.Event()
        blocked = threading.Event()
        store = AgentOrchestrationStore(tmp_path / "lease.sqlite3")
        renew = store.renew_lease
        renewals = 0
        def lose_ownership(grant, **kwargs):
            nonlocal renewals
            renewals += 1
            if blocked.is_set():
                raise ValueError("AGENT_SESSION_LEASE_FENCED")
            value = renew(grant, **kwargs)
            first_renewal.set()
            return value
        store.renew_lease = lose_ownership
        observed = []
        async def execute(request):
            assert await asyncio.to_thread(first_renewal.wait, 5)
            run = orchestrator._runs[request.run_id]
            blocked.set()
            observed.append(run.cancel_event.wait(2))
            return {"success": False, "classification": "EXPECTED_TEST_STOP"}
        orchestrator = AgentOrchestrator(change_service=_changes(tmp_path), provider_registry=_providers(tmp_path),
            store=store, run_factory=ProductAgentRun, autonomous_executor=execute,
            lease_heartbeat_interval_seconds=.01)
        workspace = tmp_path / "project"
        workspace.mkdir()
        try:
            run = await orchestrator.start_run(project_id="project", active_workspace_root=workspace,
                instruction="Repair and build", provider_id="verified_template", autonomy="build_only",
                session_id="lease-fencing")
            await orchestrator._tasks[run.run_id]
            assert observed == [True]
            assert orchestrator.get_run(run.run_id).classification == "AGENT_SESSION_LEASE_LOST"
        finally:
            await orchestrator.close()
    asyncio.run(scenario())
