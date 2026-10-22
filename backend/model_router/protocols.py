from __future__ import annotations

from dataclasses import dataclass

from .registry import ProviderRegistry


@dataclass(frozen=True, slots=True)
class ProtocolRoute:
    provider_id: str
    protocol: str


class ProtocolRegistry:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    def resolve(self, provider_id: str) -> ProtocolRoute:
        return ProtocolRoute(provider_id, self.registry.definition(provider_id).protocol)
