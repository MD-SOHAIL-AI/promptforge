from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import Any

import pytest

from backend.agent.planner import Framework, TargetBoard
from backend.contracts.execution_context import ExecutionContext


def make_context(**overrides: Any) -> ExecutionContext:
    values = {
        "task_id": "task-123",
        "project_path": "workspace/projects/weather-station",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "firmware_path": "workspace/builds/firmware.bin",
        "build_artifact": {
            "path": "workspace/builds/firmware.bin",
            "environment": "esp32dev",
            "artifact_type": "bin",
            "size_bytes": 8192,
        },
        "board_info": {
            "board_type": "ESP32",
            "port": "COM7",
            "vid": 0x10C4,
            "pid": 0xEA60,
        },
        "simulation_enabled": True,
        "metadata": {"attempt": 1, "tags": ["lab", "smoke"]},
    }
    values.update(overrides)
    return ExecutionContext(**values)


def test_safe_defaults_represent_an_unresolved_context() -> None:
    context = ExecutionContext(task_id="task-default")

    assert context.project_path is None
    assert context.target_board == "UNKNOWN"
    assert context.framework == "UNKNOWN"
    assert context.firmware_path is None
    assert context.build_artifact is None
    assert context.board_info is None
    assert context.simulation_enabled is False
    assert context.metadata == {}


def test_context_is_frozen_and_uses_slots() -> None:
    context = make_context()

    with pytest.raises(FrozenInstanceError):
        context.framework = "Arduino"  # type: ignore[misc]

    assert not hasattr(context, "__dict__")


def test_string_valued_planner_enums_are_accepted_without_import_coupling() -> None:
    context = make_context(
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
    )

    assert context.target_board == "ESP32"
    assert context.framework == "PlatformIO"


@pytest.mark.parametrize(
    "field_name",
    ["task_id", "target_board", "framework"],
)
@pytest.mark.parametrize("invalid", [None, 123, "", "   ", "bad\x00value"])
def test_rejects_invalid_required_text(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_context(**{field_name: invalid})


@pytest.mark.parametrize("field_name", ["project_path", "firmware_path"])
@pytest.mark.parametrize("invalid", [123, "", "   ", "bad\x00path"])
def test_rejects_invalid_optional_paths(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_context(**{field_name: invalid})


def test_optional_paths_accept_none_without_filesystem_access() -> None:
    context = make_context(project_path=None, firmware_path=None)

    assert context.project_path is None
    assert context.firmware_path is None


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("build_artifact", []),
        ("board_info", "COM7"),
        ("metadata", None),
    ],
)
def test_mapping_fields_require_mappings(
    field_name: str,
    invalid: object,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        make_context(**{field_name: invalid})


def test_optional_snapshot_fields_accept_none() -> None:
    context = make_context(build_artifact=None, board_info=None)

    assert context.build_artifact is None
    assert context.board_info is None


@pytest.mark.parametrize("invalid", [None, 0, 1, "true"])
def test_simulation_enabled_requires_a_boolean(invalid: object) -> None:
    with pytest.raises(ValueError, match="boolean"):
        make_context(simulation_enabled=invalid)


def test_snapshot_fields_are_defensively_copied_and_deeply_frozen() -> None:
    artifact = {"path": "firmware.bin", "segments": [{"offset": 4096}]}
    board = {"port": "COM7", "capabilities": ["wifi"]}
    metadata = {"nested": {"attempts": [1]}}

    context = make_context(
        build_artifact=artifact,
        board_info=board,
        metadata=metadata,
    )
    artifact["segments"][0]["offset"] = 8192  # type: ignore[index]
    board["capabilities"].append("ble")  # type: ignore[union-attr]
    metadata["nested"]["attempts"].append(2)  # type: ignore[index]

    assert isinstance(context.build_artifact, MappingProxyType)
    assert isinstance(context.board_info, MappingProxyType)
    assert isinstance(context.metadata, MappingProxyType)
    assert context.build_artifact["segments"][0]["offset"] == 4096
    assert context.board_info["capabilities"] == ("wifi",)
    assert context.metadata["nested"]["attempts"] == (1,)

    with pytest.raises(TypeError):
        context.build_artifact["path"] = "changed.bin"  # type: ignore[index]
    with pytest.raises(TypeError):
        context.board_info["port"] = "COM8"  # type: ignore[index]
    with pytest.raises(TypeError):
        context.metadata["new"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    "value",
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
@pytest.mark.parametrize(
    "field_name",
    ["build_artifact", "board_info", "metadata"],
)
def test_rejects_non_json_snapshot_values(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        make_context(**{field_name: value})


@pytest.mark.parametrize(
    "field_name",
    ["build_artifact", "board_info", "metadata"],
)
def test_rejects_cyclic_snapshot_values(field_name: str) -> None:
    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic

    with pytest.raises(ValueError, match="cyclic"):
        make_context(**{field_name: cyclic})


def test_to_dict_returns_canonical_json_safe_shape() -> None:
    context = make_context()

    serialized = context.to_dict()

    assert serialized == {
        "task_id": "task-123",
        "project_path": "workspace/projects/weather-station",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "firmware_path": "workspace/builds/firmware.bin",
        "build_artifact": {
            "path": "workspace/builds/firmware.bin",
            "environment": "esp32dev",
            "artifact_type": "bin",
            "size_bytes": 8192,
        },
        "board_info": {
            "board_type": "ESP32",
            "port": "COM7",
            "vid": 0x10C4,
            "pid": 0xEA60,
        },
        "simulation_enabled": True,
        "metadata": {"attempt": 1, "tags": ["lab", "smoke"]},
    }
    assert json.loads(json.dumps(serialized)) == serialized


def test_to_dict_returns_independent_mutable_snapshots() -> None:
    context = make_context(metadata={"nested": {"items": [1]}})
    serialized = context.to_dict()

    serialized["build_artifact"]["size_bytes"] = 1
    serialized["board_info"]["port"] = "COM8"
    serialized["metadata"]["nested"]["items"].append(2)

    assert context.build_artifact["size_bytes"] == 8192
    assert context.board_info["port"] == "COM7"
    assert context.metadata["nested"]["items"] == (1,)


def test_from_dict_round_trips_without_data_loss() -> None:
    original = make_context()

    restored = ExecutionContext.from_dict(original.to_dict())

    assert restored == original
    assert restored.to_dict() == original.to_dict()


@pytest.mark.parametrize("invalid", [None, [], "context"])
def test_from_dict_requires_a_mapping(invalid: object) -> None:
    with pytest.raises(ValueError, match="mapping"):
        ExecutionContext.from_dict(invalid)  # type: ignore[arg-type]


def test_from_dict_rejects_missing_and_unknown_fields() -> None:
    missing = make_context().to_dict()
    del missing["task_id"]
    with pytest.raises(ValueError, match="missing.*task_id"):
        ExecutionContext.from_dict(missing)

    unknown = make_context().to_dict()
    unknown["execution_result"] = {}
    with pytest.raises(ValueError, match="unknown.*execution_result"):
        ExecutionContext.from_dict(unknown)


def test_context_remains_hashable_despite_snapshot_mappings() -> None:
    assert isinstance(hash(make_context()), int)
