from __future__ import annotations

from backend.agent_runtime.orchestration_graph import AgentTaskNode


def test_agent_task_node_serializes_stable_graph_fields() -> None:
    node = AgentTaskNode(
        "validation",
        "validation_services",
        "Validate artifacts",
        ("implementation",),
        status="running",
        attempt=2,
        risk_level="medium",
        reviewer_required=True,
        message="checking",
    )
    node.artifact_ids.append("artifact-1")

    assert node.to_dict() == {
        "node_id": "validation",
        "role": "validation_services",
        "goal": "Validate artifacts",
        "dependencies": ["implementation"],
        "status": "running",
        "attempt": 2,
        "risk_level": "medium",
        "reviewer_required": True,
        "message": "checking",
        "artifact_ids": ["artifact-1"],
    }
