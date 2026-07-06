"""Google Antigravity / AGY CLI bridge detector."""

from __future__ import annotations

from ..base import BridgeDetector


class AntigravityCliDetector(BridgeDetector):
    provider_id = "antigravity_cli_bridge"
    display_name = "Google Antigravity / AGY CLI"
    command_names = ("agy", "antigravity")
    unknown_auth_message = (
        "AGY CLI is installed, but ForgeX cannot safely confirm authentication "
        "in detection-only mode."
    )
