"""Host-only provider deadlines; never serialized into a model payload."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    connect_seconds: float = 10.0
    first_event_seconds: float = 120.0
    idle_seconds: float = 60.0
    total_seconds: float = 300.0
    write_seconds: float = 30.0
    pool_seconds: float = 5.0

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")

    @property
    def minimum_response_seconds(self) -> float:
        return min(self.first_event_seconds, self.total_seconds)
