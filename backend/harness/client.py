"""Versioned, authenticated JSON-RPC client for the local Forge Harness host."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


HARNESS_PROTOCOL = "forgex-harness-rpc-v1"


class HarnessClientError(RuntimeError):
    """A sanitized failure crossing the Python/TypeScript harness boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class HarnessClient:
    base_url: str
    token: str
    timeout_s: float = 5.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith("http://127.0.0.1:"):
            raise ValueError("harness_url_must_be_loopback")
        if len(self.token) < 32:
            raise ValueError("harness_token_too_short")
        if self.timeout_s <= 0:
            raise ValueError("harness_timeout_invalid")

    async def health(self) -> dict[str, object]:
        return await asyncio.to_thread(self._health_sync)

    async def call(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> object:
        if not method or len(method) > 160:
            raise HarnessClientError("RPC_METHOD_INVALID", "Harness method is invalid.")
        return await asyncio.to_thread(
            self._call_sync, method, dict(params or {}), idempotency_key
        )

    def _health_sync(self) -> dict[str, object]:
        value = self._request("GET", "/health", None)
        if not isinstance(value, dict):
            raise HarnessClientError("HARNESS_HEALTH_INVALID", "Harness health response is invalid.")
        return {str(key): item for key, item in value.items()}

    def _call_sync(
        self, method: str, params: dict[str, object], idempotency_key: str | None
    ) -> object:
        request_id = str(uuid.uuid4())
        payload: dict[str, object] = {
            "protocol": HARNESS_PROTOCOL,
            "id": request_id,
            "method": method,
            "params": params,
        }
        if idempotency_key:
            payload["idempotencyKey"] = idempotency_key
        response = self._request("POST", "/rpc", payload)
        if not isinstance(response, dict) or response.get("protocol") != HARNESS_PROTOCOL:
            raise HarnessClientError("RPC_RESPONSE_INVALID", "Harness response is invalid.")
        if response.get("id") != request_id:
            raise HarnessClientError("RPC_ID_MISMATCH", "Harness response identity mismatch.")
        if response.get("ok") is True:
            return response.get("result")
        error = response.get("error")
        if not isinstance(error, dict):
            raise HarnessClientError("RPC_RESPONSE_INVALID", "Harness error response is invalid.")
        raise HarnessClientError(
            str(error.get("code") or "HARNESS_REQUEST_FAILED"),
            str(error.get("message") or "Harness request failed."),
            retryable=error.get("retryable") is True,
        )

    def _request(
        self, method: str, path: str, payload: dict[str, object] | None
    ) -> object:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            f"{self.base_url.rstrip('/')}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if body is not None else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310 -- loopback enforced above
                raw = response.read(1_048_577)
        except HTTPError as exc:
            raw = exc.read(65_537)
            try:
                decoded = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = None
            if isinstance(decoded, dict) and isinstance(decoded.get("error"), dict):
                error = decoded["error"]
                raise HarnessClientError(
                    str(error.get("code") or "HARNESS_HTTP_ERROR"),
                    str(error.get("message") or "Harness request failed."),
                    retryable=error.get("retryable") is True,
                ) from exc
            raise HarnessClientError("HARNESS_HTTP_ERROR", f"Harness returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HarnessClientError(
                "HARNESS_UNAVAILABLE", "Forge Harness is unavailable.", retryable=True
            ) from exc
        if len(raw) > 1_048_576:
            raise HarnessClientError("HARNESS_RESPONSE_TOO_LARGE", "Harness response exceeds limit.")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessClientError("HARNESS_RESPONSE_INVALID", "Harness returned invalid JSON.") from exc
