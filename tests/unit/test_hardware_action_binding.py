from __future__ import annotations

import pytest

from backend.hardware.action_binding import finalize_hardware_binding


def binding() -> dict[str, object]:
    return {
        "run_id": "run-1",
        "project_id": "project-1",
        "port": "COM7",
        "board_type": "ESP32",
        "device_identity": {"stable_id": "usb:10c4:ea60:abc", "confidence": "strong"},
        "environment": "esp32dev",
        "artifact": {"firmware_hash": "abc123"},
    }


def test_hardware_binding_is_typed_and_command_bound() -> None:
    value = finalize_hardware_binding("flash", binding())
    assert value["binding_version"] == "forgex.hardware-action.v1"
    assert value["operation"] == "flash"
    assert len(str(value["command_digest"])) == 64
    changed = binding()
    changed["environment"] = "release"
    assert finalize_hardware_binding("flash", changed)["command_digest"] != value["command_digest"]


def test_flash_binding_requires_device_and_artifact_identity() -> None:
    missing_artifact = binding()
    missing_artifact["artifact"] = {}
    with pytest.raises(ValueError, match="ARTIFACT_HASH_REQUIRED"):
        finalize_hardware_binding("flash", missing_artifact)
    missing_device = binding()
    missing_device["device_identity"] = {}
    with pytest.raises(ValueError, match="DEVICE_IDENTITY_REQUIRED"):
        finalize_hardware_binding("flash", missing_device)
