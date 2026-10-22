from __future__ import annotations

import pytest

from backend.harness.client import HARNESS_PROTOCOL, HarnessClient, HarnessClientError


@pytest.mark.asyncio
async def test_shadow_mutations_supply_stable_distinct_idempotency_keys(monkeypatch):
    from types import SimpleNamespace
    from backend.api.routes import agent_runtime
    client = HarnessClient("http://127.0.0.1:8765", "x" * 32)
    monkeypatch.setattr(agent_runtime, "services", lambda request: SimpleNamespace(harness_client=client))
    sent = []
    def response(_self, _method, _path, payload):
        assert payload.get("idempotencyKey"), "RPC_IDEMPOTENCY_KEY_REQUIRED"
        sent.append(payload)
        return {"protocol": HARNESS_PROTOCOL, "id": payload["id"], "ok": True, "result": {}}
    monkeypatch.setattr(HarnessClient, "_request", response)
    params = {"sessionId": "s", "messageId": "m1", "content": "private user text"}
    await agent_runtime._mirror_harness(None, "sessions.append", params)
    await agent_runtime._mirror_harness(None, "sessions.append", dict(reversed(list(params.items()))))
    await agent_runtime._mirror_harness(None, "sessions.append", dict(params, messageId="m2"))
    await agent_runtime._mirror_harness(None, "sessions.create", params)
    assert sent[0]["idempotencyKey"] == sent[1]["idempotencyKey"]
    assert len({item["idempotencyKey"] for item in sent}) == 3
    assert all(len(item["idempotencyKey"]) == 71 for item in sent)


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
