"""In-memory, bounded approval broker for active local agent runs."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass


APPROVAL_DECISIONS = frozenset({"approve_once", "approve_session", "decline", "cancel"})


@dataclass(frozen=True, slots=True)
class AgentApproval:
    approval_id: str
    run_id: str
    kind: str
    safe_message: str

    def to_safe_dict(self) -> dict[str, str]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "safe_message": self.safe_message,
        }


class AgentApprovalBroker:
    def __init__(self, *, timeout_seconds: float = 120.0) -> None:
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 300.0))
        self._pending: dict[str, tuple[AgentApproval, asyncio.Future[str]]] = {}
        self._session_decisions: dict[tuple[str, str], str] = {}

    async def request(self, *, run_id: str, kind: str, safe_message: str) -> str:
        session = self._session_decisions.get((run_id, kind))
        if session == "approve_session":
            return session
        approval = AgentApproval(f"approval-{uuid.uuid4().hex}", run_id, kind, safe_message[:256])
        future = asyncio.get_running_loop().create_future()
        self._pending[approval.approval_id] = (approval, future)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            return "decline"
        finally:
            self._pending.pop(approval.approval_id, None)

    def list_for_run(self, run_id: str) -> tuple[dict[str, str], ...]:
        return tuple(
            approval.to_safe_dict()
            for approval, _ in self._pending.values()
            if approval.run_id == run_id
        )

    def resolve(self, *, run_id: str, approval_id: str, decision: str) -> dict[str, str]:
        if decision not in APPROVAL_DECISIONS:
            raise ValueError("AGENT_APPROVAL_DECISION_INVALID")
        try:
            approval, future = self._pending[approval_id]
        except KeyError as exc:
            raise KeyError("AGENT_APPROVAL_NOT_FOUND") from exc
        if approval.run_id != run_id:
            raise KeyError("AGENT_APPROVAL_NOT_FOUND")
        if decision == "approve_session":
            self._session_decisions[(run_id, approval.kind)] = decision
        if not future.done():
            future.set_result(decision)
        return {**approval.to_safe_dict(), "decision": decision}

    def clear_run(self, run_id: str) -> None:
        for approval_id, (approval, future) in tuple(self._pending.items()):
            if approval.run_id != run_id:
                continue
            if not future.done():
                future.set_result("cancel")
            self._pending.pop(approval_id, None)
        for key in tuple(self._session_decisions):
            if key[0] == run_id:
                self._session_decisions.pop(key, None)
