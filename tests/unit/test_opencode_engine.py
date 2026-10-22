"""Exercise the real OpenCode bundle through ForgeX, with local fake HTTP."""
import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from backend.contracts.transport import TransportPolicy
from backend.model_router.credentials import MemoryCredentialStore
from backend.model_router.models import ModelRequest, RequestExecutionContext, StructuredOutputSpec
from backend.model_router.opencode_catalog import catalog
from backend.model_router.providers.opencode import OpenCodeProvider
from backend.model_router.registry import ProviderRegistry
from backend.model_router.router import ModelRouterService
from backend.model_router.storage import ProviderSettingsStorage
from backend.model_router.usage import UsageTracker
from backend.services.llm_service import LLMProvider, LLMProviderError, LLMTimeoutError


@contextmanager
def upstream(respond):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            respond(self, body)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}/v1"
    finally: server.shutdown(); server.server_close(); thread.join()


def registry(tmp_path):
    return ProviderRegistry(ProviderSettingsStorage(tmp_path/"models.json", credential_store=MemoryCredentialStore()))


def stream(handler, content="OK", *, finish=True):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    handler.wfile.write(('data: '+json.dumps({"choices": [{"index": 0, "delta": {"content": content}}]})+'\n\n').encode())
    if finish:
        handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\ndata: [DONE]\n\n')
    handler.wfile.flush()


def buffered(handler, content="OK", *, finish_reason="stop", reasoning=None):
    message = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    payload = {"id": "test-response", "object": "chat.completion", "created": 1,
               "model": "deepseek-chat", "choices": [{"index": 0, "message": message,
               "finish_reason": finish_reason}],
               "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps(payload).encode())
    handler.wfile.flush()


def test_real_child_normalization_and_budget_accounting(tmp_path):
    bodies = []
    def respond(handler, body): bodies.append(body); buffered(handler)
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "test-secret", "base_url": url})
        context = RequestExecutionContext()
        response = asyncio.run(OpenCodeProvider(reg, "deepseek").generate(ModelRequest(
            prompt="hello", provider_id="deepseek", model_id="deepseek-chat", execution_context=context)))
    assert response.content == "OK"
    assert response.total_tokens == 5
    assert context.attempts["used"] == 1
    assert context.diagnostics["physical_request_count"] == 1
    assert context.diagnostics["completion_received"]
    assert context.diagnostics["stream_event_count"] == 2
    assert context.budget["requests"] == 1
    assert bodies[0].get("stream", False) is False
    assert "forgex" not in json.dumps(bodies)
    assert "test-secret" not in json.dumps(context.diagnostics)


def test_actual_retry_reuses_provider_and_shares_physical_budget(tmp_path, monkeypatch):
    from backend.model_router import request_recovery
    real_sleep = asyncio.sleep
    async def fast_sleep(seconds): await real_sleep(0)
    monkeypatch.setattr(request_recovery.asyncio, "sleep", fast_sleep)
    bodies = []
    def respond(handler, body):
        bodies.append(body)
        if len(bodies) < 3:
            handler.send_response(503); handler.send_header("Retry-After", "0"); handler.end_headers(); handler.wfile.write(b'{"error":{"message":"Overloaded"}}')
        else: buffered(handler)
    reg = registry(tmp_path)
    context = RequestExecutionContext()
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "test-secret", "base_url": url})
        router = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path/"usage.jsonl"))
        response = asyncio.run(router.generate_model(ModelRequest(prompt="hello", provider_id="deepseek", model_id="deepseek-chat", execution_context=context)))
    assert response.content == "OK"
    assert len(bodies) == context.attempts["used"] == context.budget["requests"] == 3
    assert {body["model"] for body in bodies} == {"deepseek-chat"}
    assert all(body.get("stream", False) is False for body in bodies)


def test_auth_failure_is_not_retried(tmp_path):
    sent = []
    def respond(handler, body):
        sent.append(body); handler.send_response(401); handler.end_headers(); handler.wfile.write(b'{"error":{"message":"Invalid API key"}}')
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "test-secret", "base_url": url})
        router = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path/"usage.jsonl"))
        with pytest.raises(LLMProviderError) as caught:
            asyncio.run(router.generate_model(ModelRequest(prompt="hello", provider_id="deepseek", model_id="deepseek-chat")))
    assert caught.value.status_code == 401
    assert len(sent) == 1


def test_parent_deadline_reaps_child(tmp_path):
    reg = registry(tmp_path)
    def respond(handler, body): time.sleep(0.5)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "test-secret", "base_url": url})
        ctx = RequestExecutionContext(transport_policy=TransportPolicy(first_event_seconds=0.1, total_seconds=1.5))
        with pytest.raises(LLMTimeoutError) as caught:
            asyncio.run(OpenCodeProvider(reg, "deepseek").generate(ModelRequest(prompt="hello", provider_id="deepseek", model_id="deepseek-chat", execution_context=ctx)))
    assert caught.value.details["timeout_stage"] in {"response_headers", "first_event", "attempt_total"}


def test_full_upstream_catalog_and_aliases(tmp_path):
    reg = registry(tmp_path)
    assert set(catalog()) <= set(reg.provider_ids())
    for provider_id, provider in catalog().items():
        assert set(provider.get("models", {})) <= {model.model_id for model in reg.list_models(provider_id)}
    assert {m.model_id for m in reg.list_models("google")} <= {m.model_id for m in reg.list_models("gemini")}
    assert len(reg.provider_ids()) == len(set(reg.provider_ids()))


def test_provider_options_are_secure_and_schema_is_forwarded(tmp_path):
    reg = registry(tmp_path)
    reg.configure_provider("azure", {"api_key": "api-secret", "options": {"resourceName": "test-resource", "headers": {"X-Private": "header-secret"}}})
    safe = json.dumps(reg.provider("azure").to_dict()) + reg.storage.path.read_text()
    assert "header-secret" not in safe and "api-secret" not in safe
    assert reg.provider_options("azure")["resourceName"] == "test-resource"
    reg.configure_provider("openai", {"api_key": "secret"})
    payload, _ = OpenCodeProvider(reg, "openai")._payload(ModelRequest(prompt="test", model_id="gpt-4.1-mini",
        structured_output=StructuredOutputSpec("result", {"type": "object"})))
    assert payload["response_format"]["schema"] == {"type": "object"}


def test_closing_visible_stream_reaps_child_without_replay(tmp_path, monkeypatch):
    import backend.model_router.providers.opencode as boundary
    children = []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        child = await original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(boundary.asyncio, "create_subprocess_exec", spawn)
    def respond(handler, body): stream(handler, "partial", finish=False); time.sleep(0.4)
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "secret", "base_url": url})
        async def run():
            events = OpenCodeProvider(reg, "deepseek").generate_event_stream(ModelRequest(prompt="hello", model_id="deepseek-chat"))
            assert (await anext(events)).text == "partial"
            await events.aclose()
        asyncio.run(run())
    assert len(children) == 1
    assert children[0].returncode is not None


def test_unfinished_agent_attempt_is_private_until_retry_completes(tmp_path, monkeypatch):
    from backend.model_router import request_recovery
    original_sleep = asyncio.sleep
    async def fast_sleep(seconds): await original_sleep(0)
    monkeypatch.setattr(request_recovery.asyncio, "sleep", fast_sleep)
    sent = []
    def respond(handler, body):
        sent.append(body); stream(handler, "broken" if len(sent) == 1 else "complete", finish=len(sent) > 1)
    reg = registry(tmp_path)
    ctx = RequestExecutionContext()
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "secret", "base_url": url})
        owner = ModelRouterService(registry=reg, usage=UsageTracker(tmp_path/"usage.jsonl"))
        async def run():
            return [event async for event in owner.stream_model(ModelRequest(prompt="hello", provider_id="deepseek", model_id="deepseek-chat", execution_context=ctx))]
        events = asyncio.run(run())
    assert "".join(event.text or "" for event in events) == "complete"
    assert ctx.attempts["used"] == len(sent) == 2


def test_aws_keys_select_sigv4_instead_of_bearer_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    reg = registry(tmp_path)
    assert reg.api_key("amazon-bedrock") is None
    assert reg.provider("amazon-bedrock").configured
    payload, _ = OpenCodeProvider(reg, "amazon-bedrock")._payload(ModelRequest(prompt="hello", model_id="anthropic.claude-test"))
    assert "api_key" not in payload


def test_cloud_endpoints_and_options_auth_are_resolved(tmp_path, monkeypatch):
    reg = registry(tmp_path)
    reg.configure_provider("azure-cognitive-services", {"api_key": "secret", "options": {"resourceName": "my-resource"}})
    payload, _ = OpenCodeProvider(reg, "azure-cognitive-services")._payload(ModelRequest(prompt="hello", model_id="gpt-test"))
    assert payload["base_url"] == "https://my-resource.cognitiveservices.azure.com/openai/v1"
    reg.configure_provider("snowflake-cortex", {"options": {"token": "token-secret", "account": "my-account"}})
    assert reg.provider("snowflake-cortex").configured
    payload, _ = OpenCodeProvider(reg, "snowflake-cortex")._payload(ModelRequest(prompt="hello", model_id="test-model"))
    assert payload["base_url"] == "https://my-account.snowflakecomputing.com/api/v2/cortex/v1"
    assert payload["api_key"] == "token-secret"
    reg.configure_provider("amazon-bedrock", {"options": {"endpoint": "https://private-bedrock.example"}})
    payload, _ = OpenCodeProvider(reg, "amazon-bedrock")._payload(ModelRequest(prompt="hello", model_id="test-model"))
    assert payload["base_url"] == "https://private-bedrock.example"


def test_retry_header_priority_and_terminal_categories():
    from backend.model_router.retry import RetryPolicy, retry_after_ms_from_details
    assert retry_after_ms_from_details({"response_headers": {"Retry-After": "30", "Retry-After-Ms": "12.5"}}) == 13
    policy = RetryPolicy()
    assert policy.max_attempts == 6
    for category in ("authentication", "context_overflow", "content_policy", "quota_exceeded"):
        error = LLMProviderError("terminal", provider=LLMProvider.OPENAI,
            retryable=True, details={"category": category})
        assert not policy.decide(error, 1).retry
    assert not policy.decide(LLMTimeoutError("timeout", provider=LLMProvider.OPENAI, retryable=True), 6).retry


def test_sdk_reset_headers_are_canonical_for_durable_handoff():
    from backend.model_router.providers.opencode import _engine_error
    error = _engine_error({"category": "provider", "status_code": 503, "message": "overloaded", "retryable": True,
                           "response_headers": {"retry-after": "90"}}, True, time.monotonic())
    assert error.details["retry_after_ms"] == 90_000


def test_health_accepts_completed_reasoning_only_probe(tmp_path):
    def respond(handler, body):
        assert body.get("stream", False) is False
        buffered(handler, content=None, finish_reason="length", reasoning="thinking")
    reg = registry(tmp_path)
    with upstream(respond) as url:
        reg.configure_provider("deepseek", {"api_key": "secret", "base_url": url})
        health = asyncio.run(OpenCodeProvider(reg, "deepseek").health())
    assert health.ok
