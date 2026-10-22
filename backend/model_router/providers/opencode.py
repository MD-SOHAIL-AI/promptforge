"""Python boundary to the vendored OpenCode request engine.

Every child is one physical attempt. Credentials travel over stdin, never
arguments or files. Cancellation/deadlines kill and reap the owned child.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from contextlib import aclosing
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from backend.contracts.transport import TransportPolicy
from backend.observability.diagnostics import emit_diagnostic_event
from backend.runtime.async_cleanup import settle_bounded
from backend.services.llm_service import LLMConfigurationError, LLMError, LLMProvider, LLMProviderError, LLMRateLimitError, LLMResponseError, LLMTimeoutError
from ..models import CanonicalStreamEvent, ModelRequest, ProviderHealth, RequestExecutionContext, execution_value
from ..normalization import CanonicalStreamAssembler, ProviderNormalizationError
from ..opencode_catalog import QUARANTINED_SDKS, expand_endpoint, model_data, provider_data
from ..run_budget import settle_attempt
from ..retry import retry_after_ms_from_details
from ..transport_policy import request_transport_policy
from .base import BaseModelProvider
from .openai_provider import _begin_physical_attempt, _complete_physical_attempt, _fail_physical_attempt


class OpenCodeProvider(BaseModelProvider):
    def __init__(self, registry, provider_id: str):
        super().__init__(registry)
        self.provider_id = provider_id

    def _configured_health(self):
        reason = QUARANTINED_SDKS.get(provider_data(self.provider_id).get("npm"))
        if reason:
            return ProviderHealth(self.provider_id, False, "disabled", error_code="PROVIDER_SDK_QUARANTINED", message=reason)
        return super()._configured_health()

    async def list_models(self):
        self.model_list_source = "catalog"
        if self.provider_id == "ollama":
            from .ollama import OllamaProvider
            self.model_list_source = "provider"
            return await OllamaProvider(self.registry).list_models()
        if self.provider_id == "lmstudio":
            from .lmstudio import LMStudioProvider
            self.model_list_source = "provider"
            return await LMStudioProvider(self.registry).list_models()
        if self.provider_id == "nvidia_nim":
            from .nvidia_nim import NvidiaNimProvider
            self.model_list_source = "provider"
            return await NvidiaNimProvider(self.registry).list_models()
        if self.provider_id == "cerebras":
            from .cerebras import CerebrasProvider
            self.model_list_source = "provider"
            return await CerebrasProvider(self.registry).list_models()
        def cached_models():
            with self.registry.storage.read_snapshot():
                if self.registry.storage.provider_models(self.provider_id):
                    self.model_list_source = "cache"
                return self.registry.list_models(self.provider_id)
        return await asyncio.to_thread(cached_models)

    async def health(self):
        missing = self._configured_health()
        if missing:
            return missing
        # Test the same protocol and authentication as real requests. Many
        # providers do not implement GET /models; it is not a health endpoint.
        started = time.monotonic()
        try:
            await self.generate(ModelRequest(prompt="Reply with OK.", provider_id=self.provider_id,
                model_id=self.registry.default_model(self.provider_id), max_tokens=16,
                execution_context=RequestExecutionContext(attempts={"used": 0, "limit": 1}, managed=False,
                    transport_policy=TransportPolicy(first_event_seconds=20, idle_seconds=10, total_seconds=30))))
            return ProviderHealth(self.provider_id, True, "connected", int((time.monotonic()-started)*1000))
        except LLMError as exc:
            if exc.code == "MODEL_OUTPUT_TRUNCATED":
                # A tiny readiness probe may spend its output allowance on
                # reasoning; accepted/completed transport still proves access.
                return ProviderHealth(self.provider_id, True, "connected", int((time.monotonic()-started)*1000))
            return ProviderHealth(self.provider_id, False, "authentication_required" if exc.code == "PROVIDER_AUTHENTICATION_FAILED" else "error", int((time.monotonic()-started)*1000), exc.code, str(exc))

    async def generate(self, request):
        started = time.monotonic()
        request = replace(request, metadata={**request.metadata, "forgex_transport_stream": False})
        assembler = CanonicalStreamAssembler()
        async with aclosing(self.generate_event_stream(request)) as events:
            async for event in events:
                assembler.add(event)
        try:
            response = assembler.response(provider_id=self.provider_id, model_id=request.model_id or self.registry.default_model(self.provider_id))
        except ProviderNormalizationError as exc:
            raise LLMResponseError(str(exc), provider=LLMProvider.OPENAI, code=exc.code, retryable=False) from exc
        diagnostics = execution_value(request, "diagnostics", "forgex_attempt_diagnostics", {})
        return replace(response, latency_ms=int((time.monotonic()-started)*1000), request_id=diagnostics.get("provider_request_id"))

    async def generate_stream(self, request):
        async with aclosing(self.generate_event_stream(request)) as events:
            async for event in events:
                if event.type == "text_delta" and event.text:
                    yield event.text

    def _payload(self, request):
        definition = self.registry.definition(self.provider_id)
        data = provider_data(self.provider_id)
        model_id = request.model_id or self.registry.default_model(self.provider_id)
        facts = model_data(self.provider_id, model_id)
        options = self.registry.provider_options(self.provider_id)
        saved_endpoint = self.registry.storage.provider_settings(self.provider_id).get("base_url")
        endpoint = expand_endpoint(saved_endpoint or (options.get("endpoint") if self.provider_id == "amazon-bedrock" else None) or options.get("baseURL") or facts.get("provider", {}).get("api") or self.registry.base_url(self.provider_id), options)
        npm = facts.get("provider", {}).get("npm") or data.get("npm", "@ai-sdk/openai-compatible")
        if npm in QUARANTINED_SDKS:
            raise LLMConfigurationError(QUARANTINED_SDKS[npm], provider=LLMProvider.OPENAI,
                code="PROVIDER_SDK_QUARANTINED")
        if definition.protocol in {"openai-completions", "openai-responses", "anthropic-messages"} and self.provider_id not in self.registry.builtin_provider_ids():
            npm = {"anthropic-messages": "@ai-sdk/anthropic", "openai-responses": "@ai-sdk/openai"}.get(definition.protocol, "@ai-sdk/openai-compatible")
        if self.provider_id == "ollama": endpoint = (endpoint or "http://127.0.0.1:11434").rstrip("/").removesuffix("/v1") + "/v1"
        if self.provider_id == "azure-cognitive-services" and not endpoint and options.get("resourceName"):
            endpoint = f"https://{options['resourceName']}.cognitiveservices.azure.com/openai/v1"
        if self.provider_id.startswith("github-copilot") and options.get("enterpriseUrl") and not saved_endpoint:
            domain = options["enterpriseUrl"].removeprefix("https://").removeprefix("http://").rstrip("/")
            endpoint = f"https://copilot-api.{domain}"
        # Enforce transport security at execution too: persisted settings or
        # environment overrides can predate the configuration validation.
        if endpoint:
            parsed_endpoint = urlsplit(endpoint)
            if parsed_endpoint.scheme == "http" and parsed_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise LLMConfigurationError("Remote provider endpoints require HTTPS", provider=LLMProvider.OPENAI,
                    code="PROVIDER_ENDPOINT_INSECURE")
        policy = request_transport_policy(request, provider_id=self.provider_id, model_id=model_id,
                                          reasoning=self.registry.model_capabilities(self.provider_id, model_id).reasoning,
                                          free=self.registry.model_is_free(self.provider_id, model_id))
        if request.execution_context is not None and request.execution_context.transport_policy is None:
            # Recovery admission must reserve the same window as the transport.
            request.execution_context.transport_policy = policy
        payload = {"provider_id": self.provider_id, "model_id": facts.get("provider", {}).get("id") or model_id,
            "npm": npm, "protocol": definition.protocol, "api_key": self.registry.api_key(self.provider_id),
            "base_url": endpoint, "options": options, "headers": options.get("headers", {}),
            "prompt": request.prompt, "system_prompt": request.system_prompt,
            "stream": request.metadata.get("forgex_transport_stream", True),
            "temperature": request.temperature, "temperature_supported": facts.get("temperature", True),
            "max_tokens": request.max_tokens, "output_limit": facts.get("limit", {}).get("output") or None,
            "messages": request.metadata.get("forgex_messages"), "tools": request.metadata.get("forgex_native_tools"),
            "tool_choice": request.metadata.get("forgex_tool_choice", "auto"),
            "provider_options": options.get("providerOptions"),
            "http_body": options.get("body"),
            "policy": {"header_ms": policy.first_event_seconds*1000, "first_event_ms": policy.first_event_seconds*1000,
                       "idle_ms": policy.idle_seconds*1000, "total_ms": policy.total_seconds*1000}}
        # A provider-native schema is optional; the agent also validates the
        # finished ToolPlan. Host metadata is never forwarded as HTTP fields.
        spec = request.structured_output
        if spec:
            caps = self.registry.model_capabilities(self.provider_id, model_id)
            if caps.json_schema:
                payload["response_format"] = {"type": "json", "schema": dict(spec.json_schema)}
            elif spec.allow_prompt_fallback:
                payload["system_prompt"] = (request.system_prompt or "") + "\nReturn only a JSON object matching this schema: " + json.dumps(dict(spec.json_schema))
        # NVIDIA-specific chat_template_kwargs are not a provider-wide API.
        # Restrict this compatibility profile to models we explicitly qualified.
        nemotron_3_profile = model_id.casefold() in {
            "nvidia/nemotron-3-super-120b-a12b",
            "nvidia/nemotron-3-ultra-550b-a55b",
        }
        if self.provider_id in {"nvidia_nim", "nvidia"} and nemotron_3_profile and (request.task_type == "planning" or payload.get("tools")):
            if request.temperature is None:
                payload["temperature"] = 1.0
            payload["top_p"] = 0.95
            payload["http_body"] = {"chat_template_kwargs": {"enable_thinking": False, "force_nonempty_content": True}, **(payload.get("http_body") or {})}
        if self.provider_id == "openrouter" and request.metadata.get("model_output_repair"):
            # Verified against OpenRouter's endpoints API on 2026-10-05. Do
            # not infer effort support from the generic reasoning capability.
            supported = {"nvidia/nemotron-3-ultra-550b-a55b:free"}
            provider_options = payload.get("provider_options") or {}
            native_options = provider_options.get("openrouter") or {}
            explicit_body = payload.get("http_body") or {}
            explicit_reasoning = any(key in explicit_body for key in ("reasoning", "reasoning_effort", "include_reasoning")) or any(
                key in native_options for key in ("reasoning", "reasoning_effort", "include_reasoning"))
            if model_id in supported and not explicit_reasoning:
                payload["provider_options"] = {**provider_options, "openrouter": {**native_options, "reasoning": {"effort": "low"}}}
        return {key: value for key, value in payload.items() if value is not None}, policy

    async def generate_event_stream(self, request):
        model_id = request.model_id or self.registry.default_model(self.provider_id)
        availability = self.registry.model_availability(self.provider_id, model_id)
        if availability.get("availability") == "retired":
            raise LLMConfigurationError(availability["reason"], provider=LLMProvider.OPENAI,
                code="MODEL_RETIRED", details={"origin": "provider_model", "http_status": 410})
        unavailable = self._configured_health()
        if unavailable:
            raise LLMConfigurationError(unavailable.message or "Configure the selected model provider in Models.",
                provider=LLMProvider.OPENAI, code=unavailable.error_code or "NOT_CONFIGURED")
        entry = Path(__file__).resolve().parents[3] / "dist" / "model-router" / "worker.cjs"
        executable = os.getenv("FORGEX_NODE_EXECUTABLE") or shutil.which("node")
        if not executable or not entry.is_file():
            raise LLMConfigurationError("OpenCode model engine is missing. Run npm install and npm run build:model-router.", provider=LLMProvider.OPENAI, code="MODEL_ENGINE_UNAVAILABLE")
        try:
            payload, policy = self._payload(request)
        except (ValueError, TypeError) as exc:
            raise LLMConfigurationError(str(exc), provider=LLMProvider.OPENAI) from exc
        deadline = execution_value(request, "deadline", "forgex_turn_deadline")
        total = policy.total_seconds
        total_stage = "attempt_total"
        if isinstance(deadline, (float, int)) and not isinstance(deadline, bool):
            remaining = deadline-time.monotonic()
            if remaining <= total:
                total = remaining
                total_stage = request.execution_context.deadline_kind if request.execution_context else "turn_deadline"
        if total <= 0:
            raise _deadline_error(total_stage, reached=False, duration_ms=0)
        payload["policy"].update(total_ms=total*1000, total_stage=total_stage)
        try:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
        except (ValueError, TypeError) as exc:
            raise LLMConfigurationError(str(exc), provider=LLMProvider.OPENAI) from exc
        if len(encoded) > 8_388_608:
            raise LLMConfigurationError("Model request exceeds limit", provider=LLMProvider.OPENAI)
        diagnostics = _begin_physical_attempt(request)
        output_limit = payload.get("output_limit")
        effective_output = min(request.max_tokens or 4096, output_limit or float("inf"))
        if diagnostics is not None:
            diagnostics.update(requested_output_tokens=request.max_tokens or 4096,
                effective_output_tokens=effective_output, finish_reason=None,
                input_tokens=None, output_tokens=None, reasoning_tokens=None, total_tokens=None,
                text_event_count=0, reasoning_event_count=0, tool_event_count=0,
                worker_exit_code=None, worker_cleanup_incomplete=False,
                worker_phase=None, worker_failure_kind=None)
        process = None
        stderr_task = None
        stderr_sample = bytearray()
        worker_phase = "spawn"
        started = time.monotonic()
        done = False
        first_event = False
        last_progress = None
        timeout_stage = "attempt_total"
        token_usage = {}
        assembler = CanonicalStreamAssembler()
        reached = False
        failure_code = None
        try:
            expires = min(started + total, deadline) if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) else started + total
            timeout_stage = total_stage if expires-time.monotonic() <= policy.connect_seconds else "worker_spawn"
            process = await asyncio.wait_for(asyncio.create_subprocess_exec(executable, str(entry),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=_worker_environment(),
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}), limit=8_388_608),
                timeout=max(0.001, min(policy.connect_seconds, expires-time.monotonic())))
            async def discard_stderr():
                while chunk := await process.stderr.read(4096):
                    if len(stderr_sample) < 16384:
                        stderr_sample.extend(chunk[:16384 - len(stderr_sample)])
            stderr_task = asyncio.create_task(discard_stderr())
            worker_phase = "input"
            timeout_stage = total_stage if expires-time.monotonic() <= policy.write_seconds else "worker_write"
            process.stdin.write(encoded)
            await asyncio.wait_for(process.stdin.drain(),
                timeout=max(0.001, min(policy.write_seconds, expires-time.monotonic())))
            process.stdin.close()
            worker_phase = "response"
            while True:
                timeout = expires-time.monotonic()
                timeout_stage = total_stage
                if not first_event and started + policy.first_event_seconds < expires:
                    timeout = started + policy.first_event_seconds-time.monotonic()
                    timeout_stage = "first_event"
                elif not done and last_progress is not None and last_progress + policy.idle_seconds < expires:
                    timeout = last_progress + policy.idle_seconds-time.monotonic()
                    timeout_stage = "stream_idle"
                if timeout <= 0: raise asyncio.TimeoutError()
                line = await asyncio.wait_for(process.stdout.readline(), timeout)
                if not line: break
                try: value = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise LLMResponseError("Model engine returned an invalid event", provider=LLMProvider.OPENAI, retryable=False) from exc
                kind = value.get("type")
                if kind == "transport":
                    reached = True
                    if diagnostics is not None:
                        diagnostics.update(response_headers_received=True, http_status=value.get("status"), provider_request_id=value.get("request_id"), request_reached_provider=True)
                    continue
                if kind == "transport_chunk": continue
                if kind == "model_progress":
                    if done:
                        raise LLMResponseError("Model engine emitted progress after completion", provider=LLMProvider.OPENAI, retryable=False)
                    # Tool arguments stream before a complete safe tool call.
                    # Their progress keeps the watchdog alive without releasing
                    # partial calls into the agent or exposing arguments in logs.
                    first_event = True
                    last_progress = time.monotonic()
                    if diagnostics is not None:
                        duration = int((last_progress-started)*1000)
                        if diagnostics.get("first_event_ms") is None:
                            diagnostics["first_event_ms"] = duration
                        diagnostics["last_event_ms"] = duration
                        diagnostics["stream_event_count"] = int(diagnostics.get("stream_event_count") or 0) + 1
                    continue
                if kind == "transport_error":
                    raise LLMTimeoutError("Provider read timed out", provider=LLMProvider.OPENAI, retryable=True,
                        details={"timeout_stage": value.get("stage"), "request_reached_provider": reached, "duration_ms": int((time.monotonic()-started)*1000)})
                if kind == "error": raise _engine_error(value.get("error", {}), reached, started)
                if kind not in {"text_delta", "reasoning_delta", "tool_call_delta", "done"}:
                    raise LLMResponseError("Unknown model engine event", provider=LLMProvider.OPENAI, retryable=False)
                if done:
                    raise LLMResponseError("Model engine emitted events after completion", provider=LLMProvider.OPENAI, retryable=False)
                event = CanonicalStreamEvent(**{key: item for key, item in value.items() if key in CanonicalStreamEvent.__dataclass_fields__})
                meaningful = kind == "done" or kind == "tool_call_delta" or bool(event.text)
                if meaningful:
                    first_event = True
                    last_progress = time.monotonic()
                if diagnostics is not None:
                    duration = int((time.monotonic()-started)*1000)
                    if meaningful:
                        if diagnostics.get("first_event_ms") is None:
                            diagnostics["first_event_ms"] = duration
                        diagnostics["last_event_ms"] = duration
                    diagnostics["stream_event_count"] = int(diagnostics.get("stream_event_count") or 0) + 1
                    diagnostics["completion_received"] = kind == "done"
                    diagnostics["duration_ms"] = duration
                    counter = {"text_delta": "text_event_count", "reasoning_delta": "reasoning_event_count", "tool_call_delta": "tool_event_count"}.get(kind)
                    if counter:
                        diagnostics[counter] += 1
                    if kind == "done":
                        diagnostics["finish_reason"] = event.finish_reason if event.finish_reason in {
                            "stop", "length", "max_tokens", "incomplete", "MAX_TOKENS", "tool_calls", "tool-calls", "end_turn", "other", "unknown"} else "unknown"
                    diagnostics.update({key: item for key, item in event.usage.items()
                        if key in {"input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"}
                        and type(item) is int and item >= 0})
                done = kind == "done"
                token_usage.update(event.usage)
                assembler.add(event)
                if kind == "done":
                    try:
                        response = assembler.response(provider_id=self.provider_id, model_id=request.model_id or "model")
                    except ProviderNormalizationError as exc:
                        raise LLMResponseError(str(exc), provider=LLMProvider.OPENAI, code=exc.code,
                            retryable=False, details={"origin": "model_output"}) from exc
                    if response.truncated:
                        raise LLMResponseError("Model output was truncated before a complete response.",
                            provider=LLMProvider.OPENAI, code="MODEL_OUTPUT_TRUNCATED", retryable=False,
                            details={"origin": "model_output"})
                yield event
            status = await asyncio.wait_for(process.wait(), max(0.01, expires-time.monotonic()))
            if status != 0:
                raise _worker_error(reached, started, worker_phase)
            if not done:
                raise LLMResponseError("Provider stream ended before completion", provider=LLMProvider.OPENAI, code="PROVIDER_STREAM_INVALID", retryable=True)
            _complete_physical_attempt(diagnostics)
            settle_attempt(request, token_usage.get("output_tokens"), complete=assembler.response(provider_id=self.provider_id, model_id=request.model_id or "model").truncated is False)
        except (BrokenPipeError, ConnectionResetError, FileNotFoundError, PermissionError) as exc:
            error = _worker_error(reached, started, worker_phase)
            failure_code = error.code
            _fail_physical_attempt(diagnostics, error)
            raise error from exc
        except asyncio.TimeoutError as exc:
            if time.monotonic() >= expires:
                timeout_stage = total_stage
            error = _deadline_error(timeout_stage, reached=reached, duration_ms=int((time.monotonic()-started)*1000))
            failure_code = error.code
            _fail_physical_attempt(diagnostics, error)
            raise error from exc
        except Exception as exc:
            failure_code = str(getattr(exc, "code", type(exc).__name__))
            if failure_code == "MODEL_RETIRED":
                try:
                    self.registry.mark_model_retired(self.provider_id, model_id, endpoint=payload.get("base_url"))
                except (OSError, RuntimeError):
                    # Catalog persistence must not replace the provider's error.
                    pass
            _fail_physical_attempt(diagnostics, exc)
            raise
        except asyncio.CancelledError:
            failure_code = "CANCELLED"
            raise
        finally:
            if not done and failure_code is None:
                failure_code = "CANCELLED"  # Consumer closed the stream before completion.
            if process is not None and process.returncode is None:
                try: process.kill()
                except ProcessLookupError: pass
                completed, _ = await settle_bounded(process.wait(), 2.0)
                if diagnostics is not None and not completed:
                    diagnostics["worker_cleanup_incomplete"] = True
            if stderr_task is not None:
                try:
                    # Let already buffered exit diagnostics drain before the
                    # bounded cleanup cancels a reader that does not finish.
                    await settle_bounded(stderr_task, 0.25)
                except (asyncio.CancelledError, OSError):
                    pass
            if diagnostics is not None:
                if failure_code:
                    diagnostics["last_attempt_succeeded"] = False
                diagnostics["worker_exit_code"] = process.returncode if process is not None else None
                diagnostics["worker_phase"] = worker_phase
                if failure_code == "MODEL_WORKER_FAILED":
                    # Raw stderr can contain provider credentials or project
                    # data. Export only a recognized runtime failure label.
                    stderr_label = bytes(stderr_sample).lower()
                    diagnostics["worker_failure_kind"] = (
                        "memory_limit" if any(label in stderr_label for label in
                            (b"heap out of memory", b"allocation failed", b"enomem")) else
                        "invalid_runtime_options" if any(label in stderr_label for label in
                            (b"bad option", b"not allowed in node_options")) else "abnormal_exit")
                diagnostics["duration_ms"] = max(0, int((time.monotonic()-started)*1000))
                fields = {key: diagnostics.get(key) for key in (
                    "physical_attempt_id", "requested_output_tokens", "effective_output_tokens", "finish_reason",
                    "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens", "http_status",
                    "provider_request_id", "completion_received", "stream_event_count", "text_event_count",
                    "reasoning_event_count", "tool_event_count", "worker_exit_code", "worker_cleanup_incomplete",
                    "worker_phase", "worker_failure_kind",
                    "first_event_ms", "last_event_ms", "timeout_stage", "duration_ms")}
                emit_diagnostic_event(component="provider", event="provider.attempt.failed" if failure_code else "provider.attempt.completed",
                    level="WARNING" if failure_code else "INFO", provider=self.provider_id, model=request.model_id,
                    run_id=request.metadata.get("run_id"), turn_id=request.metadata.get("turn_id"),
                    error_code=failure_code, recovery_sequence=request.metadata.get("recovery_sequence", "primary"), **fields)


def _worker_environment():
    environment = {**os.environ, "ELECTRON_RUN_AS_NODE": "1"}
    options = environment.get("NODE_OPTIONS", "")
    # Small nursery spaces reduce native commit spikes when Windows is under
    # memory pressure. Respect explicit operator heap/nursery choices.
    for flag, value in (("max-old-space-size", 1024), ("max-semi-space-size", 1)):
        pattern = "--" + flag.replace("-", "[-_]") + r"(?:=|\s|$)"
        if not re.search(pattern, options):
            options += f" --{flag}={value}"
    environment["NODE_OPTIONS"] = options.strip()
    return environment


def _worker_error(reached, started, phase="response"):
    return LLMConfigurationError("The local model worker exited unexpectedly. Saved work can be continued; "
        "check available memory and the worker exit diagnostics.", provider=LLMProvider.OPENAI,
        code="MODEL_WORKER_FAILED", details={"origin": "model_worker", "category": "configuration",
            "request_reached_provider": reached, "worker_phase": phase,
            "duration_ms": int((time.monotonic()-started)*1000)})


def _deadline_error(stage, *, reached, duration_ms, details=None):
    parent = stage in {"job_deadline", "turn_deadline"}
    return LLMTimeoutError("Model request exceeded its deadline", provider=LLMProvider.OPENAI,
        code={"job_deadline": "AGENT_RUNTIME_LIMIT_EXCEEDED", "turn_deadline": "AGENT_TURN_LIMIT_EXCEEDED"}.get(stage, "PROVIDER_TIMEOUT"),
        retryable=not parent, details={"timeout_stage": stage, "origin": stage if parent else "provider_transport",
                                     "request_reached_provider": reached, "duration_ms": duration_ms, **(details or {})})


def _engine_error(value, reached, started):
    if value.get("timeout_stage"):
        return _deadline_error(value["timeout_stage"], reached=reached, duration_ms=int((time.monotonic()-started)*1000),
            details={"provider_request_id": value.get("request_id"), "response_headers": value.get("response_headers"),
                     "http_status": value.get("status_code")})
    category = value.get("category", "provider")
    details = {"origin": "opencode", "category": category, "provider_request_id": value.get("request_id"),
               "response_headers": value.get("response_headers"), "retry_after_ms": value.get("retry_after_ms"),
               "timeout_stage": value.get("timeout_stage"), "http_status": value.get("status_code"),
               "request_reached_provider": reached, "duration_ms": int((time.monotonic()-started)*1000)}
    details["retry_after_ms"] = retry_after_ms_from_details(details)
    if value.get("failure_shape") == "invalid_stream_event":
        details["failure_shape"] = "invalid_stream_event"
    if category == "model_unavailable":
        details["origin"] = "provider_model"
    cls = LLMRateLimitError if category == "rate_limit" else LLMResponseError if category == "invalid_provider_output" else LLMTimeoutError if value.get("timeout_stage") else LLMProviderError
    code = {"authentication": "PROVIDER_AUTHENTICATION_FAILED", "context_overflow": "CONTEXT_OVERFLOW", "quota_exceeded": "PROVIDER_QUOTA_EXCEEDED", "content_policy": "PROVIDER_CONTENT_POLICY", "rate_limit": "RATE_LIMITED", "model_unavailable": "MODEL_RETIRED"}.get(category, value.get("code", "PROVIDER_REQUEST_FAILED"))
    return cls(str(value.get("message", "Provider request failed")), provider=LLMProvider.OPENAI, code=code,
               status_code=value.get("status_code"), retryable=bool(value.get("retryable")), details=details)
