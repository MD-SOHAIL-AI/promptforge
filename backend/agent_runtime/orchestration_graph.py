"""Task graph primitives for ForgeX agent orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AgentTaskNode:
    node_id: str
    role: str
    goal: str
    dependencies: tuple[str, ...] = ()
    status: str = "pending"
    attempt: int = 0
    risk_level: str = "unassessed"
    reviewer_required: bool = False
    message: str | None = None
    artifact_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "goal": self.goal,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "attempt": self.attempt,
            "risk_level": self.risk_level,
            "reviewer_required": self.reviewer_required,
            "message": self.message,
            "artifact_ids": list(self.artifact_ids),
        }
