"""Explicit provider registration kept deliberately separate from routing."""

from __future__ import annotations

from collections.abc import Iterable

from .contracts import BridgeProvider
from .errors import BridgeDomainError, BridgeErrorCode
from .validation import KNOWN_PROVIDER_IDS, validate_provider_id


class BridgeProviderRegistry:
    def __init__(self, *, allowed_provider_ids: frozenset[str] = KNOWN_PROVIDER_IDS) -> None:
        self._allowed_provider_ids = allowed_provider_ids
        self._providers: dict[str, BridgeProvider] = {}

    @property
    def routing_enabled(self) -> bool:
        return False

    def register(self, provider: BridgeProvider) -> None:
        provider_id = validate_provider_id(provider.provider_id, allowed=self._allowed_provider_ids)
        if provider_id in self._providers:
            raise BridgeDomainError(
                BridgeErrorCode.INVALID_REQUEST,
                "A bridge provider with this identifier is already registered.",
            )
        if not isinstance(provider, BridgeProvider):
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Provider does not satisfy the bridge contract.")
        capabilities = provider.capabilities()
        if capabilities.provider_id != provider_id or not capabilities.sandbox_required:
            raise BridgeDomainError(BridgeErrorCode.INVALID_REQUEST, "Provider capabilities do not match registration.")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> BridgeProvider:
        validated = validate_provider_id(provider_id, allowed=self._allowed_provider_ids)
        try:
            return self._providers[validated]
        except KeyError as exc:
            raise BridgeDomainError(BridgeErrorCode.PROVIDER_UNAVAILABLE) from exc

    def list_registered(self) -> tuple[BridgeProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def is_routable(self, provider_id: str) -> bool:
        validate_provider_id(provider_id, allowed=self._allowed_provider_ids)
        return False

    def register_many(self, providers: Iterable[BridgeProvider]) -> None:
        for provider in providers:
            self.register(provider)
