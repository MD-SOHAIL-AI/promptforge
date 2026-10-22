"""Authenticated client boundary for the Forge Harness sidecar."""

from .client import HarnessClient, HarnessClientError

__all__ = ["HarnessClient", "HarnessClientError"]
