from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

import pytest

from backend.contracts.task import Task, TaskPriority, TaskSource


CREATED_AT = datetime(2026, 6, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)


def make_task(**overrides: Any) -> Task:
    values = {
        "task_id": "task-123",
        "prompt": "Build an ESP32 temperature monitor",
        "source": TaskSource.USER,
        "priority": TaskPriority.HIGH,
        "created_at": CREATED_AT,
        "metadata": {"project": "weather", "pins": [4, 5]},
    }
    values.update(overrides)
    return Task(**values)


def test_enum_values_are_stable_strings() -> None:
    assert [item.value for item in TaskPriority] == [
        "LOW",
        "NORMAL",
        "HIGH",
        "CRITICAL",
    ]
    assert [item.value for item in TaskSource] == ["USER", "API", "SYSTEM"]
    assert isinstance(TaskPriority.NORMAL, str)
    assert isinstance(TaskSource.USER, str)


def test_constructor_defaults_are_suitable_for_user_tasks() -> None:
    before = datetime.now(timezone.utc)
    task = Task(task_id="task-default", prompt="Blink an LED")
    after = datetime.now(timezone.utc)

    assert task.source is TaskSource.USER
    assert task.priority is TaskPriority.NORMAL
    assert before <= task.created_at <= after
    assert task.metadata == {}


def test_task_is_frozen_and_uses_slots() -> None:
    task = make_task()

    with pytest.raises(FrozenInstanceError):
        task.prompt = "changed"  # type: ignore[misc]

    assert not hasattr(task, "__dict__")


def test_metadata_is_defensively_copied_and_deeply_frozen() -> None:
    source = {
        "nested": {"enabled": True},
        "items": ["one", {"value": 2}],
    }
    task = make_task(metadata=source)

    source["nested"]["enabled"] = False  # type: ignore[index]
    source["items"].append("three")  # type: ignore[union-attr]

    assert isinstance(task.metadata, MappingProxyType)
    assert task.metadata["nested"]["enabled"] is True
    assert task.metadata["items"] == ("one", MappingProxyType({"value": 2}))
    with pytest.raises(TypeError):
        task.metadata["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        task.metadata["nested"]["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("field_name", ["task_id", "prompt"])
@pytest.mark.parametrize("invalid", [None, 123, "", "   ", "bad\x00value"])
def test_rejects_invalid_text_fields(field_name: str, invalid: object) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_task(**{field_name: invalid})


@pytest.mark.parametrize(
    ("field_name", "invalid", "message"),
    [
        ("source", "USER", "TaskSource"),
        ("priority", "HIGH", "TaskPriority"),
        ("created_at", "2026-06-10T12:30:45Z", "datetime"),
        ("created_at", datetime(2026, 6, 10), "timezone-aware"),
        ("metadata", [], "mapping"),
    ],
)
def test_constructor_rejects_invalid_field_types(
    field_name: str,
    invalid: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_task(**{field_name: invalid})


def test_created_at_is_normalized_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    task = make_task(
        created_at=datetime(2026, 6, 10, 18, 0, 45, tzinfo=offset)
    )

    assert task.created_at == datetime(
        2026, 6, 10, 12, 30, 45, tzinfo=timezone.utc
    )
    assert task.created_at.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "metadata",
    [
        {"": "value"},
        {1: "value"},
        {"value": object()},
        {"value": {1, 2}},
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": b"bytes"},
    ],
)
def test_rejects_metadata_that_is_not_json_compatible(
    metadata: object,
) -> None:
    with pytest.raises(ValueError):
        make_task(metadata=metadata)


def test_rejects_cyclic_metadata() -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="cyclic"):
        make_task(metadata=cyclic)


def test_to_dict_returns_canonical_json_safe_shape() -> None:
    task = make_task(
        metadata={
            "nested": {"enabled": True},
            "items": (1, 2),
            "optional": None,
        }
    )

    serialized = task.to_dict()

    assert serialized == {
        "task_id": "task-123",
        "prompt": "Build an ESP32 temperature monitor",
        "source": "USER",
        "priority": "HIGH",
        "created_at": "2026-06-10T12:30:45.123456Z",
        "metadata": {
            "nested": {"enabled": True},
            "items": [1, 2],
            "optional": None,
        },
    }
    assert json.loads(json.dumps(serialized)) == serialized


def test_to_dict_returns_independent_mutable_containers() -> None:
    task = make_task(metadata={"nested": {"items": [1]}})
    serialized = task.to_dict()

    serialized["metadata"]["nested"]["items"].append(2)

    assert task.metadata["nested"]["items"] == (1,)


def test_from_dict_round_trips_without_data_loss() -> None:
    original = make_task(
        source=TaskSource.API,
        priority=TaskPriority.CRITICAL,
    )

    restored = Task.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()


def test_from_dict_accepts_offset_timestamp_and_normalizes_it() -> None:
    data = make_task().to_dict()
    data["created_at"] = "2026-06-10T18:00:45.123456+05:30"

    task = Task.from_dict(data)

    assert task.created_at == CREATED_AT
    assert task.to_dict()["created_at"] == "2026-06-10T12:30:45.123456Z"


@pytest.mark.parametrize("invalid", [None, [], "task"])
def test_from_dict_requires_a_mapping(invalid: object) -> None:
    with pytest.raises(ValueError, match="mapping"):
        Task.from_dict(invalid)  # type: ignore[arg-type]


def test_from_dict_rejects_missing_and_unknown_fields() -> None:
    missing = make_task().to_dict()
    del missing["prompt"]
    with pytest.raises(ValueError, match="missing.*prompt"):
        Task.from_dict(missing)

    unknown = make_task().to_dict()
    unknown["plan"] = {}
    with pytest.raises(ValueError, match="unknown.*plan"):
        Task.from_dict(unknown)


@pytest.mark.parametrize(
    ("field_name", "invalid", "message"),
    [
        ("source", "CLI", "source must be one of"),
        ("priority", "URGENT", "priority must be one of"),
        ("created_at", 123, "ISO 8601 string"),
        ("created_at", "not-a-date", "valid ISO 8601"),
        ("created_at", "2026-06-10T12:30:45", "timezone offset"),
    ],
)
def test_from_dict_rejects_invalid_serialized_values(
    field_name: str,
    invalid: object,
    message: str,
) -> None:
    data = make_task().to_dict()
    data[field_name] = invalid

    with pytest.raises(ValueError, match=message):
        Task.from_dict(data)


def test_task_remains_hashable_despite_metadata() -> None:
    assert isinstance(hash(make_task()), int)
