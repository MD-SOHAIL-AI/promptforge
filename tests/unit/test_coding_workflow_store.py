from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.agent_runtime.coding_workflow_store import (
    CODING_WORKFLOW_EVENT_SCHEMA_VERSION,
    CODING_WORKFLOW_RUN_SCHEMA_VERSION,
    EVENT_SEQUENCE_INVALID,
    INVALID_STATE_TRANSITION,
    OPERATION_IN_PROGRESS,
    RECORD_INVALID,
    RUN_ALREADY_EXISTS,
    RUN_STATUS_INVALID,
    STORE_CORRUPT,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)


def make_store(tmp_path: Path) -> CodingWorkflowStore:
    return CodingWorkflowStore.from_state_directory(tmp_path / ".promptforge" / "state")


def run_record(run_id: str = "run-1") -> CodingWorkflowRunRecord:
    return CodingWorkflowRunRecord(
        run_id=run_id,
        task_id="task-1",
        project_id="project-1",
        provider_id="fake_api_coding_agent",
        provider_type="api_coding_agent",
        status="awaiting_apply",
        generation_status="review_created",
        review_id="review-1",
        next_action="await_user_approval",
        files_changed=("platformio.ini", "src/main.cpp"),
        safe_summary="Created a bounded review.",
        safe_message="Review is waiting for approval.",
        metadata={"experimental_workflow": True},
    )


def event_record(run_id: str, sequence: int, event_type: str) -> CodingWorkflowEventRecord:
    return CodingWorkflowEventRecord(
        event_id=f"event-{run_id}-{sequence}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        stage="generation",
        status="running" if sequence == 1 else "awaiting_apply",
        safe_message=f"Safe event {sequence}.",
        metadata={"provider_id": "fake_api_coding_agent"},
    )


def test_missing_store_files_return_empty_results(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.list_runs() == ()
    assert store.list_events("missing-run") == ()
    assert not store.runs_path.exists()
    assert not store.events_path.exists()


def test_create_get_list_and_schema_versions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = run_record()
    second = replace(first, run_id="run-2", review_id="review-2")
    store.create_run(first)
    store.create_run(second)

    assert store.get_run("run-1") == first
    assert {record.run_id for record in store.list_runs()} == {"run-1", "run-2"}
    assert first.schema_version == CODING_WORKFLOW_RUN_SCHEMA_VERSION
    assert store.runs_path.name == "coding-workflow-runs.jsonl"


def test_duplicate_run_id_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())
    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.create_run(run_record())
    assert exc_info.value.code == RUN_ALREADY_EXISTS


def test_append_only_events_are_monotonic_and_unique(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())
    first = event_record("run-1", 1, "provider.selected")
    second = event_record("run-1", 2, "review.created")
    store.append_event(first)
    store.append_event(second)

    assert store.list_events("run-1") == (first, second)
    assert first.schema_version == CODING_WORKFLOW_EVENT_SCHEMA_VERSION
    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.append_event(event_record("run-1", 4, "generation.completed"))
    assert exc_info.value.code == EVENT_SEQUENCE_INVALID


def test_mark_run_appends_a_new_run_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    initial = CodingWorkflowRunRecord(
        run_id="run-1",
        provider_id="fake_api_coding_agent",
        provider_type="api_coding_agent",
        status="running",
        generation_status="running",
    )
    store.create_run(initial)
    updated = store.mark_run_completed_or_failed(
        "run-1",
        status="awaiting_apply",
        generation_status="review_created",
        review_id="review-1",
        next_action="await_user_approval",
        files_changed=("src/main.cpp",),
        safe_summary="Review created.",
        safe_message="Approval required.",
    )

    assert store.get_run("run-1") == updated
    assert store.list_runs() == (updated,)
    assert len(store.runs_path.read_text(encoding="utf-8").splitlines()) == 2


def test_transition_run_validates_status_and_supports_apply_states(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())
    applying = store.transition_run(
        "run-1",
        expected_statuses=("awaiting_apply",),
        status="applying",
        generation_status="review_created",
        next_action=None,
        safe_message="Applying.",
    )
    completed = store.transition_run(
        "run-1",
        expected_statuses=("applying",),
        status="awaiting_build",
        generation_status="review_created",
        next_action="run_build",
        safe_message="Waiting for build.",
    )
    assert applying.status == "applying"
    assert completed.status == "awaiting_build"
    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.transition_run(
            "run-1",
            expected_statuses=("awaiting_apply",),
            status="applying",
            generation_status="review_created",
            next_action=None,
        )
    assert exc_info.value.code == RUN_STATUS_INVALID


def test_operation_lock_rejects_duplicate_and_can_be_released(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())

    store.acquire_operation_lock("run-1", "apply")
    assert store.is_operation_locked("run-1") is True
    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.acquire_operation_lock("run-1", "build")
    assert exc_info.value.code == OPERATION_IN_PROGRESS

    store.release_operation_lock("run-1", "apply")
    assert store.is_operation_locked("run-1") is False
    store.acquire_operation_lock("run-1", "build")
    store.release_operation_lock("run-1")


def test_transition_run_rejects_invalid_explicit_transition(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())

    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.transition_run(
            "run-1",
            expected_statuses=("awaiting_apply",),
            status="awaiting_flash",
            generation_status="review_created",
            next_action="confirm_flash",
        )
    assert exc_info.value.code == INVALID_STATE_TRANSITION


def test_transition_run_allows_guarded_same_status_metadata_update(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())

    updated = store.transition_run(
        "run-1",
        expected_statuses=("awaiting_apply",),
        status="awaiting_apply",
        generation_status="review_created",
        next_action="await_user_approval",
        metadata={"attempt": 2},
    )

    assert updated.status == "awaiting_apply"
    assert updated.next_action == "await_user_approval"
    assert updated.metadata == {"attempt": 2}


def test_transition_run_supports_explicit_build_and_flash_gate_states(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run(run_record())
    store.transition_run(
        "run-1",
        expected_statuses=("awaiting_apply",),
        status="applying",
        generation_status="review_created",
        next_action=None,
    )
    store.transition_run(
        "run-1",
        expected_statuses=("applying",),
        status="awaiting_build",
        generation_status="review_created",
        next_action="run_build",
    )
    store.transition_run(
        "run-1",
        expected_statuses=("awaiting_build",),
        status="building",
        generation_status="review_created",
        next_action=None,
    )
    final = store.transition_run(
        "run-1",
        expected_statuses=("building",),
        status="awaiting_flash",
        generation_status="review_created",
        next_action="confirm_flash",
    )
    assert final.status == "awaiting_flash"
    assert final.next_action == "confirm_flash"


def test_transition_run_supports_explicit_flash_and_monitor_gate_states(tmp_path: Path) -> None:
    initial = run_record()
    store = make_store(tmp_path)
    store.create_run(initial)
    for expected, status, next_action in (
        ("awaiting_apply", "applying", None),
        ("applying", "awaiting_build", "run_build"),
        ("awaiting_build", "building", None),
        ("building", "awaiting_flash", "confirm_flash"),
        ("awaiting_flash", "flashing", None),
        ("flashing", "awaiting_monitor", "open_monitor"),
    ):
        final = store.transition_run(
            "run-1",
            expected_statuses=(expected,),
            status=status,
            generation_status="review_created",
            next_action=next_action,
        )
    assert final.status == "awaiting_monitor"
    assert final.next_action == "open_monitor"


def test_transition_run_supports_monitoring_and_completed_states(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    initial = run_record()
    store.create_run(initial)
    transitions = (
        ("awaiting_apply", "applying", None),
        ("applying", "awaiting_build", "run_build"),
        ("awaiting_build", "building", None),
        ("building", "awaiting_flash", "confirm_flash"),
        ("awaiting_flash", "flashing", None),
        ("flashing", "awaiting_monitor", "open_monitor"),
        ("awaiting_monitor", "monitoring", None),
        ("monitoring", "completed", None),
    )
    for expected, status, next_action in transitions:
        final = store.transition_run(
            "run-1", expected_statuses=(expected,), status=status,
            generation_status="review_created", next_action=next_action,
        )
    assert final.status == "completed"
    assert final.next_action is None


@pytest.mark.parametrize("filename", ["coding-workflow-runs.jsonl", "coding-workflow-events.jsonl"])
def test_malformed_store_is_reported_and_preserved(tmp_path: Path, filename: str) -> None:
    store = make_store(tmp_path)
    path = store.runs_path if filename.startswith("coding-workflow-runs") else store.events_path
    path.parent.mkdir(parents=True)
    path.write_text("{malformed\n", encoding="utf-8")

    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        store.list_runs() if path == store.runs_path else store.list_events("run-1")
    assert exc_info.value.code == STORE_CORRUPT
    assert path.read_text(encoding="utf-8") == "{malformed\n"


@pytest.mark.parametrize(
    "metadata",
    [
        {"source_body": "int main() {}"},
        {"api_key": "not-persisted"},
        {"note": "OPENAI_API_KEY=not-persisted"},
        {"note": "x" * 2_001},
    ],
)
def test_unsafe_or_unbounded_metadata_is_rejected(metadata: dict[str, object]) -> None:
    with pytest.raises(CodingWorkflowStoreError) as exc_info:
        replace(run_record(), metadata=metadata)
    assert exc_info.value.code == RECORD_INVALID


def test_persisted_records_exclude_prompt_source_and_secret_material(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record = run_record()
    events = (
        event_record(record.run_id, 1, "provider.selected"),
        event_record(record.run_id, 2, "review.created"),
    )
    store.persist_run(record, events)

    stored = store.runs_path.read_text(encoding="utf-8") + store.events_path.read_text(encoding="utf-8")
    assert "raw_prompt" not in stored
    assert "source_body" not in stored
    assert "api_key" not in stored
    assert "OPENAI_API_KEY" not in stored
