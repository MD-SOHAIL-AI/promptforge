import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.agent_runtime.failure_contract import failure_details
from backend.model_router.models import ModelRequest, RequestExecutionContext
from backend.model_router.providers.opencode import OpenCodeProvider, _worker_environment
from backend.model_router.retry import RetryPolicy
from backend.services.llm_service import LLMConfigurationError
from tests.unit.test_opencode_engine import registry


def test_worker_memory_defaults_respect_existing_operator_settings(monkeypatch):
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    assert "--max-semi-space-size=1" in _worker_environment()["NODE_OPTIONS"]
    monkeypatch.setenv("NODE_OPTIONS", "--max_old_space_size=512 --max-semi-space-size 8 --trace-warnings")
    assert _worker_environment()["NODE_OPTIONS"] == "--max_old_space_size=512 --max-semi-space-size 8 --trace-warnings"


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError])
def test_worker_pipe_failure_is_local_accounted_and_does_not_replay(tmp_path, monkeypatch, failure):
    import backend.model_router.providers.opencode as module
    reg = registry(tmp_path)
    reg.configure_provider("deepseek", {"api_key": "worker-fixture-private-key"})
    class Input:
        def write(self, _):
            raise failure("fixture pipe closed")
    children = []
    async def spawn(*args, **kwargs):
        stderr = asyncio.StreamReader()
        stderr.feed_eof()
        child = SimpleNamespace(stdin=Input(), stderr=stderr, returncode=3221226505)
        children.append(child)
        return child
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    ctx = RequestExecutionContext()
    with pytest.raises(LLMConfigurationError) as caught:
        asyncio.run(OpenCodeProvider(reg, "deepseek").generate(ModelRequest(
            prompt="create firmware", model_id="deepseek-chat", execution_context=ctx)))
    error = caught.value
    assert error.code == "MODEL_WORKER_FAILED" and error.details["origin"] == "model_worker"
    assert not error.details["request_reached_provider"]
    assert not RetryPolicy().decide(error, 1).retry
    assert len(children) == ctx.attempts["used"] == ctx.budget["requests"] == 1
    assert ctx.diagnostics["worker_exit_code"] == 3221226505
    assert ctx.diagnostics["worker_phase"] == "input"
    assert ctx.diagnostics["worker_failure_kind"] == "abnormal_exit"
    assert "worker-fixture-private-key" not in str(error) + json.dumps(ctx.diagnostics)
    public = failure_details(error.code, status="failed")
    assert public["category"] == "runtime" and public["normalized_code"] == "MODEL_WORKER_FAILED"


@pytest.mark.parametrize("stderr,label", [
    (b"FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory", "memory_limit"),
    (b"node: --invalid-option is not allowed in NODE_OPTIONS", "invalid_runtime_options"),
    (b"unexpected local runtime exit", "abnormal_exit"),
])
def test_worker_exit_labels_are_bounded_and_never_export_stderr(tmp_path, monkeypatch, stderr, label):
    import backend.model_router.providers.opencode as module
    reg = registry(tmp_path)
    reg.configure_provider("deepseek", {"api_key": "worker-fixture-private-key"})
    class Input:
        def write(self, _): pass
        async def drain(self): pass
        def close(self): pass
    async def spawn(*args, **kwargs):
        out, err = asyncio.StreamReader(), asyncio.StreamReader()
        out.feed_eof()
        err.feed_data(stderr + b" worker-fixture-private-key private-project-content" + b"!" * 20000)
        err.feed_eof()
        async def wait(): return 1
        return SimpleNamespace(stdin=Input(), stdout=out, stderr=err, returncode=1, wait=wait)
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    ctx = RequestExecutionContext()
    with pytest.raises(LLMConfigurationError) as caught:
        asyncio.run(OpenCodeProvider(reg, "deepseek").generate(ModelRequest(
            prompt="create firmware", model_id="deepseek-chat", execution_context=ctx)))
    assert ctx.diagnostics["worker_phase"] == "response"
    assert ctx.diagnostics["worker_failure_kind"] == label
    assert caught.value.code == "MODEL_WORKER_FAILED"
    exported = str(caught.value) + json.dumps(ctx.diagnostics)
    assert "worker-fixture-private-key" not in exported and "private-project-content" not in exported
