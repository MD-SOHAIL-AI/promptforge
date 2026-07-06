"""Bridge detection orchestration."""

from __future__ import annotations

from .base import CommandRunner
from .models import BridgeDetectionResult
from .providers import AntigravityCliDetector, ClaudeCodeDetector, CodexDetector


class BridgeDetectionService:
    """Detect installed bridge CLIs without executing prompts."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        platform_name: str | None = None,
    ) -> None:
        self._detectors = (
            CodexDetector(command_runner=command_runner, platform_name=platform_name),
            ClaudeCodeDetector(command_runner=command_runner, platform_name=platform_name),
            AntigravityCliDetector(command_runner=command_runner, platform_name=platform_name),
        )

    def detect_all(self) -> list[BridgeDetectionResult]:
        return [detector.detect() for detector in self._detectors]

    def detect_provider(self, provider_id: str) -> BridgeDetectionResult:
        for detector in self._detectors:
            if detector.provider_id == provider_id:
                return detector.detect()
        raise ValueError(f"Unknown bridge provider: {provider_id}")
