"""One-time, fingerprint-bound approval records for hardware actions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from .orchestration_store import AgentOrchestrationStore, utc_now


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    action_type: str
    fingerprint: str
    status: str
    expires_at: str
    created_at: str
    summary: str

    def to_dict(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "action_type": self.action_type,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "summary": self.summary,
        }


class ApprovalService:
    def __init__(self, store: AgentOrchestrationStore, *, ttl_seconds: int = 900) -> None:
        self.store = store
        self.ttl_seconds = max(60, min(int(ttl_seconds), 3600))

    def issue(self, *, run_id: str, action_type: str, binding: Mapping[str, object], summary: str) -> ApprovalRequest:
        created = datetime.now(timezone.utc)
        request = ApprovalRequest(
            approval_id=f"approval-{uuid.uuid4().hex}",
            run_id=run_id,
            action_type=action_type,
            fingerprint=self.fingerprint(action_type, binding),
            status="pending",
            expires_at=(created + timedelta(seconds=self.ttl_seconds)).isoformat().replace("+00:00", "Z"),
            created_at=created.isoformat().replace("+00:00", "Z"),
            summary=" ".join(summary.split())[:512],
        )
        self.store.save_approval(request.to_dict())
        return request

    def consume(self, approval_id: str, *, action_type: str, binding: Mapping[str, object]) -> dict[str, object]:
        payload = self.store.consume_approval(
            approval_id,
            fingerprint=self.fingerprint(action_type, binding),
        )
        return payload

    @staticmethod
    def fingerprint(action_type: str, binding: Mapping[str, object]) -> str:
        document = json.dumps(
            {"action_type": action_type, "binding": dict(binding)},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(document.encode("utf-8")).hexdigest()
