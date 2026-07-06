"""Bridge detector provider implementations."""

from .antigravity_cli import AntigravityCliDetector
from .agy_generic import AGYBridgeProvider
from .claude_code import ClaudeCodeDetector
from .codex import CodexDetector

__all__ = ["AGYBridgeProvider", "AntigravityCliDetector", "ClaudeCodeDetector", "CodexDetector"]
