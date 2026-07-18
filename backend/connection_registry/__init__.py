"""Authoritative authentication and transport read model."""
from .registry import (AuthState, CredentialStatus, ConnectionRecord, ConnectionRegistry, ConnectionRegistryError, ConnectionRegistryFailure)
from .compatibility import LegacyProviderRegistryFacade
__all__ = ["AuthState", "CredentialStatus", "ConnectionRecord", "ConnectionRegistry", "ConnectionRegistryError", "ConnectionRegistryFailure", "LegacyProviderRegistryFacade"]
