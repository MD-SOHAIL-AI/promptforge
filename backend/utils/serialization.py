"""Shared immutable JSON-compatible serialization helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any


def _freeze_mapping(
    value: Mapping[object, Any],
    *,
    path: str,
    active: set[int],
) -> Mapping[str, Any]:
    # fix: centralize recursive mapping freezing for existing callers.
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} cannot contain cyclic references")
    active.add(identity)
    try:
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_json_value(
                item,
                path=f"{path}.{key}",
                active=active,
            )
        return MappingProxyType(frozen)
    finally:
        active.remove(identity)


def _freeze_json_value(
    value: Any,
    *,
    path: str,
    active: set[int],
) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite number")
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value, path=path, active=active)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} cannot contain cyclic references")
        active.add(identity)
        try:
            return tuple(
                _freeze_json_value(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    raise ValueError(f"{path} contains a non-JSON-compatible value")
