from __future__ import annotations

import pytest

from backend.harness.client import HARNESS_PROTOCOL, HarnessClient, HarnessClientError


def test_harness_client_requires_loopback_and_strong_token() -> None:
    with pytest.raises(ValueError, match="loopback"):
        HarnessClient("https://example.com", "x" * 32)
    with pytest.raises(ValueError, match="too_short"):
        HarnessClient("http://127.0.0.1:8765", "short")


@pytest.mark.asyncio
async def test_harness_client_validates_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HarnessClient("http://127.0.0.1:8765", "x" * 32)

    def response(_self: HarnessClient, _method: str, _path: str, payload: dict[str, object] | None) -> object:
        assert payload is not None
        return {
            "protocol": HARNESS_PROTOCOL,
            "id": payload["id"],
            "ok": True,
            "result": {"ready": True},
        }

    monkeypatch.setattr(HarnessClient, "_request", response)
    assert await client.call("harness.status") == {"ready": True}


@pytest.mark.asyncio
async def test_harness_client_normalizes_remote_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    client = HarnessClient("http://127.0.0.1:8765", "x" * 32)

    def response(_self: HarnessClient, _method: str, _path: str, payload: dict[str, object] | None) -> object:
        assert payload is not None
        return {
            "protocol": HARNESS_PROTOCOL,
            "id": payload["id"],
            "ok": False,
            "error": {"code": "RPC_METHOD_NOT_FOUND", "message": "Unknown method", "retryable": False},
        }

    monkeypatch.setattr(HarnessClient, "_request", response)
    with pytest.raises(HarnessClientError) as captured:
        await client.call("missing")
    assert captured.value.code == "RPC_METHOD_NOT_FOUND"
