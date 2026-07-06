"""Claude Code CLI bridge detector."""

from __future__ import annotations

from ..base import BridgeDetector


class ClaudeCodeDetector(BridgeDetector):
    provider_id = "claude_code_bridge"
    display_name = "Claude Code"
    command_names = ("claude",)
    unknown_auth_message = (
        "Claude Code is installed, but ForgeX cannot safely confirm authentication "
        "in detection-only mode."
    )
