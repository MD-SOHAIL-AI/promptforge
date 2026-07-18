from pathlib import Path

from backend.release.cutover_audit import _artifact_persistence_fail_closed


def test_production_authoritative_artifact_paths_are_ast_proven_fail_closed():
    root = Path(__file__).resolve().parents[2]
    passed, evidence, blockers = _artifact_persistence_fail_closed(root)
    assert passed is True, (evidence, blockers)
    assert blockers == ()


def test_architecture_guard_detects_broad_artifact_exception_suppression(tmp_path):
    engine = tmp_path / "backend" / "agent_runtime" / "workflow_engine.py"
    state = tmp_path / "backend" / "state" / "durable_workflow.py"
    engine.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    engine.write_text(
        "class AgentWorkflowEngine:\n"
        " def _record_artifact(self):\n"
        "  try:\n"
        "   self.repo.insert('artifacts', {})\n"
        "  except Exception:\n"
        "   pass\n",
        encoding="utf-8",
    )
    state.write_text("class DurableWorkflowStateMachine:\n pass\n", encoding="utf-8")

    passed, _, blockers = _artifact_persistence_fail_closed(tmp_path)
    assert passed is False
    assert "broad_artifact_exception_suppressed:_record_artifact" in blockers
    assert "separate_or_best_effort_artifact_insert" in blockers