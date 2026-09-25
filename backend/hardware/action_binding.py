"""Canonical approval bindings for every physical-device mutation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


HARDWARE_MUTATION_ACTIONS = frozenset(
    {
        "flash",
        "erase",
        "reset",
        "ota",
        "debug_attach",
        "fuse_change",
        "security_change",
        "power_control",
    }
)


def finalize_hardware_binding(
    action: str, binding: Mapping[str, object]
) -> dict[str, object]:
    """Return the exact, command-bound document fingerprinted by approval.

    The digest represents the typed host operation, not a model-provided shell
    string. Host executors must derive argv from the same typed fields.
    """

    if action not in HARDWARE_MUTATION_ACTIONS:
        raise ValueError("HARDWARE_ACTION_NOT_APPROVABLE")
    run_id = _required_text(binding.get("run_id"), "run_id")
    project_id = _required_text(binding.get("project_id"), "project_id")
    device = binding.get("device_identity")
    if not isinstance(device, Mapping) or not str(device.get("stable_id") or ""):
        raise ValueError("HARDWARE_DEVICE_IDENTITY_REQUIRED")
    artifact = binding.get("artifact")
    artifact_hash = _artifact_hash(artifact)
    if action in {"flash", "ota"} and artifact_hash is None:
        raise ValueError("HARDWARE_ARTIFACT_HASH_REQUIRED")
    command_spec = {
        "operation": action,
        "run_id": run_id,
        "project_id": project_id,
        "device_stable_id": str(device["stable_id"]),
        "board_type": binding.get("board_type"),
        "environment": binding.get("environment"),
        "artifact_hash": artifact_hash,
    }
    command_digest = hashlib.sha256(
        json.dumps(
            command_spec,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **dict(binding),
        "binding_version": "forgex.hardware-action.v1",
        "operation": action,
        "command_digest": command_digest,
    }


def _artifact_hash(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("firmware_hash", "artifact_hash", "checksum"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"HARDWARE_{field.upper()}_REQUIRED")
    return value.strip()
