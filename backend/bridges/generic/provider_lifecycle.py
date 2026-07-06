"""Strict provider-readiness lifecycle, separate from per-run state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ProviderLifecycleState(str, Enum):
    DISABLED = "DISABLED"
    DETECTED = "DETECTED"
    HELP_INSPECTED = "HELP_INSPECTED"
    HEADLESS_MODE_FOUND = "HEADLESS_MODE_FOUND"
    AUTH_UNKNOWN = "AUTH_UNKNOWN"
    AUTH_READY = "AUTH_READY"
    NATIVE_WRITE_TESTED = "NATIVE_WRITE_TESTED"
    NATIVE_WRITE_PASSED = "NATIVE_WRITE_PASSED"
    NATIVE_WRITE_FAILED = "NATIVE_WRITE_FAILED"
    ARTIFACT_IMPORT_TESTED = "ARTIFACT_IMPORT_TESTED"
    ARTIFACT_IMPORT_PASSED = "ARTIFACT_IMPORT_PASSED"
    REVIEW_ELIGIBLE = "REVIEW_ELIGIBLE"
    HARDENING_ELIGIBLE = "HARDENING_ELIGIBLE"
    PRODUCTION_CANDIDATE = "PRODUCTION_CANDIDATE"
    PAUSED = "PAUSED"


ALLOWED_PROVIDER_TRANSITIONS: dict[ProviderLifecycleState, frozenset[ProviderLifecycleState]] = {
    ProviderLifecycleState.DISABLED: frozenset({ProviderLifecycleState.DETECTED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.DETECTED: frozenset({ProviderLifecycleState.HELP_INSPECTED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.HELP_INSPECTED: frozenset({ProviderLifecycleState.HEADLESS_MODE_FOUND, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.HEADLESS_MODE_FOUND: frozenset({ProviderLifecycleState.AUTH_UNKNOWN, ProviderLifecycleState.AUTH_READY, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.AUTH_UNKNOWN: frozenset({ProviderLifecycleState.AUTH_READY, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.AUTH_READY: frozenset({ProviderLifecycleState.NATIVE_WRITE_TESTED, ProviderLifecycleState.ARTIFACT_IMPORT_TESTED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.NATIVE_WRITE_TESTED: frozenset({ProviderLifecycleState.NATIVE_WRITE_PASSED, ProviderLifecycleState.NATIVE_WRITE_FAILED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.NATIVE_WRITE_PASSED: frozenset({ProviderLifecycleState.REVIEW_ELIGIBLE, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.NATIVE_WRITE_FAILED: frozenset({ProviderLifecycleState.ARTIFACT_IMPORT_TESTED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.ARTIFACT_IMPORT_TESTED: frozenset({ProviderLifecycleState.ARTIFACT_IMPORT_PASSED, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.ARTIFACT_IMPORT_PASSED: frozenset({ProviderLifecycleState.REVIEW_ELIGIBLE, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.REVIEW_ELIGIBLE: frozenset({ProviderLifecycleState.HARDENING_ELIGIBLE, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.HARDENING_ELIGIBLE: frozenset({ProviderLifecycleState.PRODUCTION_CANDIDATE, ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.PRODUCTION_CANDIDATE: frozenset({ProviderLifecycleState.PAUSED}),
    ProviderLifecycleState.PAUSED: frozenset(),
}


@dataclass(slots=True)
class ProviderLifecycle:
    provider_id: str
    state: ProviderLifecycleState = ProviderLifecycleState.DISABLED
    history: list[ProviderLifecycleState] = field(default_factory=lambda: [ProviderLifecycleState.DISABLED])

    def transition(self, target: ProviderLifecycleState) -> ProviderLifecycleState:
        if target not in ALLOWED_PROVIDER_TRANSITIONS[self.state]:
            raise ValueError("provider_lifecycle_invalid_transition")
        self.state = target
        self.history.append(target)
        return target

    def pause(self) -> ProviderLifecycleState:
        if self.state is ProviderLifecycleState.PAUSED:
            return self.state
        return self.transition(ProviderLifecycleState.PAUSED)
