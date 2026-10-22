"""Local desktop authentication and loopback request validation.

ForgeX's backend intentionally listens only on loopback.  Loopback alone is not an
authorization boundary: any process or browser page on the machine can try to talk
to it.  The Electron desktop therefore supplies a per-launch random token and an
HttpOnly cookie.  Browser JavaScript never needs to read the token.
"""

from __future__ import annotations

import hmac
import os
from urllib.parse import urlsplit

from fastapi import Request, WebSocket

from .errors import APIError

LOCAL_SESSION_COOKIE = "forgex_local_session"
LOCAL_TOKEN_ENV = "FORGEX_LOCAL_TOKEN"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def require_local_http_request(request: Request, *, purpose: str = "ForgeX operation") -> None:
    """Require loopback Host/Origin and an authenticated desktop session."""

    expected = _configured_token(request.app.state)
    require_local_address(request, purpose=purpose)
    if not expected:
        raise APIError(503, "LOCAL_TOKEN_REQUIRED", f"{purpose} requires a configured local API token.")
    if not _request_token_matches(request, expected):
        raise APIError(
            401,
            "LOCAL_AUTH_REQUIRED",
            f"{purpose} requires the authenticated local ForgeX desktop session.",
        )


def require_local_address(request: Request, *, purpose: str = "ForgeX operation") -> None:
    _validate_host(request.headers.get("host"), purpose=purpose)
    _validate_origin(request.headers.get("origin"), purpose=purpose)


def local_http_error(request: Request) -> APIError | None:
    """Return a structured auth error for global middleware without raising through middleware."""

    try:
        require_local_http_request(request, purpose="ForgeX API access")
    except APIError as exc:
        return exc
    return None


def websocket_is_authorized(websocket: WebSocket) -> tuple[bool, str]:
    """Validate a WebSocket handshake before accept()."""

    expected = _configured_token(websocket.app.state)
    try:
        _validate_host(websocket.headers.get("host"), purpose="ForgeX WebSocket")
        _validate_origin(websocket.headers.get("origin"), purpose="ForgeX WebSocket")
    except APIError as exc:
        return False, exc.code
    if not expected:
        return False, "LOCAL_TOKEN_REQUIRED"
    if not _websocket_token_matches(websocket, expected):
        return False, "LOCAL_AUTH_REQUIRED"
    return True, ""


def local_token_fingerprint(token: str | None) -> str | None:
    if not token:
        return None
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def _configured_token(state: object) -> str | None:
    services = getattr(state, "services", None)
    value = getattr(services, "local_api_token", None) if services is not None else None
    if value is None:
        # Compatibility for deliberately hand-built test/external apps.
        value = getattr(state, "local_api_token", None)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _request_token_matches(request: Request, expected: str) -> bool:
    return _candidate_token(request.headers.get("authorization"), request.cookies.get(LOCAL_SESSION_COOKIE), expected)


def _websocket_token_matches(websocket: WebSocket, expected: str) -> bool:
    return _candidate_token(websocket.headers.get("authorization"), websocket.cookies.get(LOCAL_SESSION_COOKIE), expected)


def _candidate_token(authorization: str | None, cookie: str | None, expected: str) -> bool:
    bearer: str | None = None
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() == "bearer" and value.strip():
            bearer = value.strip()
    for candidate in (bearer, cookie):
        if candidate and hmac.compare_digest(candidate, expected):
            return True
    return False


def _validate_host(value: str | None, *, purpose: str) -> None:
    hostname, port = _host_parts(value)
    if hostname in _LOOPBACK_HOSTS and port == _backend_port():
        return
    raise APIError(
        403,
        "LOCAL_HOST_REJECTED",
        f"{purpose} is restricted to the local ForgeX backend.",
    )


def _validate_origin(value: str | None, *, purpose: str) -> None:
    # Native clients and server-side proxy requests may omit Origin. Token
    # authentication still applies to every non-health operation.
    if not value:
        return
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        parsed = None
    try:
        if (
            parsed
            and parsed.scheme in {"http", "https"}
            and (parsed.hostname or "").casefold() in _LOOPBACK_HOSTS
            and parsed.port in {_backend_port(), _frontend_port()}
            and not parsed.username
            and not parsed.password
            and not parsed.path.strip("/")
            and not parsed.query
            and not parsed.fragment
        ):
            return
    except ValueError:
        pass
    raise APIError(
        403,
        "REMOTE_ORIGIN_REJECTED",
        f"{purpose} is restricted to the local ForgeX desktop application.",
    )


def _host_parts(host_header: str | None) -> tuple[str, int | None]:
    if not host_header:
        return "", None
    value = host_header.strip()
    try:
        parsed = urlsplit(f"//{value}")
        if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return "", None
        return (parsed.hostname or "").casefold(), parsed.port
    except ValueError:
        return "", None


def _backend_port() -> int:
    return _configured_port("FORGEX_BACKEND_PORT", 8000)


def _frontend_port() -> int:
    return _configured_port("FORGEX_FRONTEND_PORT", 3000)


def _configured_port(name: str, default: int) -> int:
    try:
        port = int(os.getenv(name, str(default)))
    except ValueError:
        return -1
    return port if 1 <= port <= 65535 else -1
