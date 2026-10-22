"""High-confidence secret detection used at model I/O boundaries.

The scanner intentionally favors precision over recall to avoid blocking ordinary
source code and example configuration.  It targets credential formats that are
unlikely to occur accidentally.
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai_compatible_key", re.compile(r"\bsk-(?:[A-Za-z0-9_-]{20,})\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b")),
)


def find_high_confidence_secrets(text: str) -> tuple[str, ...]:
    kinds = [kind for kind, pattern in _PATTERNS if pattern.search(text)]
    return tuple(dict.fromkeys(kinds))


def has_high_confidence_secret(text: str) -> bool:
    return bool(find_high_confidence_secrets(text))
