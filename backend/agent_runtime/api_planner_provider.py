"""Shared, disabled-by-default chat-completions API planner adapter."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from .tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
from ..provider_runtime.validation import safe_relative_path


API_PRODUCT_SMOKE_PATH = "API_PROVIDER_SMOKE.txt"
API_PRODUCT_SMOKE_CONTENT = "ForgeX API planner product runtime smoke completed."
MAX_API_RESPONSE_BYTES = 64 * 1024


class ApiPlannerClassification(str, Enum):
    READY = "API_PROVIDER_READY"
    CONFIRMATION_REQUIRED = "API_PROVIDER_CONFIRMATION_REQUIRED"
    DISABLED = "API_PROVIDER_DISABLED"
    KEY_MISSING = "API_PROVIDER_KEY_MISSING"
    MODEL_MISSING = "API_PROVIDER_MODEL_MISSING"
    AUTH_INVALID = "API_AUTH_INVALID"
    BILLING_REQUIRED = "API_BILLING_REQUIRED"
    MODEL_UNAVAILABLE = "API_MODEL_UNAVAILABLE"
    RATE_LIMITED = "API_RATE_LIMITED"
    QUOTA_EXCEEDED = "API_QUOTA_EXCEEDED"
    NETWORK_ERROR = "API_NETWORK_ERROR"
    RESPONSE_INVALID = "API_RESPONSE_INVALID"
    TOOLPLAN_INVALID = "API_TOOLPLAN_INVALID"
    TOOLPLAN_UNSAFE = "API_TOOLPLAN_UNSAFE"
    SANDBOX_WRITE_PASS = "API_SANDBOX_WRITE_PASS"
    SANDBOX_NO_CHANGES = "API_SANDBOX_NO_CHANGES"
    SANDBOX_EXTRA_CHANGES = "API_SANDBOX_EXTRA_CHANGES"
    UNSAFE_ABORTED = "API_PROVIDER_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "API_PROVIDER_UNKNOWN_SAFE_FAILURE"


class ApiPlannerError(ValueError):
    def __init__(self, classification: ApiPlannerClassification, safe_code: str) -> None:
        self.classification = classification.value
        self.safe_code = safe_code
        super().__init__(safe_code)


class ApiTransportHTTPError(Exception):
    def __init__(
        self,
        status: int,
        safe_body: str = "",
        request_id: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.status = status
        self.safe_body = safe_body[:4096]
        self.request_id = _safe_request_id(request_id)
        self.retry_after_seconds = retry_after_seconds if retry_after_seconds and 0 < retry_after_seconds <= 3600 else None
        super().__init__(f"http_status_{status}")


@dataclass(frozen=True, slots=True)
class ApiPlannerConfig:
    provider_id: str
    display_name: str
    base_url: str
    api_key_env: str
    model_env: str
    provider_flag: str
    default_model: str | None
    fallback_api_key_env: str | None = None
    protocol: str = "openai"

    @property
    def endpoint(self) -> str:
        suffix = "/messages" if self.protocol == "anthropic" else "/chat/completions"
        return f"{self.base_url.rstrip('/')}{suffix}"


API_PLANNER_CONFIGS: dict[str, ApiPlannerConfig] = {
    "anthropic": ApiPlannerConfig("anthropic", "Anthropic API", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "FORGEX_ANTHROPIC_MODEL", "FORGEX_ENABLE_ANTHROPIC_PROVIDER", "claude-sonnet-4-5", protocol="anthropic"),
    "cerebras": ApiPlannerConfig("cerebras", "Cerebras API", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "FORGEX_CEREBRAS_MODEL", "FORGEX_ENABLE_CEREBRAS_PROVIDER", "gpt-oss-120b"),
    "gemini": ApiPlannerConfig("gemini", "Gemini API", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "FORGEX_GEMINI_MODEL", "FORGEX_ENABLE_GEMINI_PROVIDER", "gemini-2.5-flash"),
    "groq": ApiPlannerConfig("groq", "Groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "FORGEX_GROQ_MODEL", "FORGEX_ENABLE_GROQ_PROVIDER", "llama-3.1-8b-instant"),
    "openrouter": ApiPlannerConfig("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "FORGEX_OPENROUTER_MODEL", "FORGEX_ENABLE_OPENROUTER_PROVIDER", "openrouter/auto"),
    "openai": ApiPlannerConfig("openai", "OpenAI API", "https://api.openai.com/v1", "OPENAI_API_KEY", "FORGEX_OPENAI_MODEL", "FORGEX_ENABLE_OPENAI_PROVIDER", "gpt-4.1-mini"),
    "nvidia_nim": ApiPlannerConfig("nvidia_nim", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "FORGEX_NVIDIA_NIM_MODEL", "FORGEX_ENABLE_NVIDIA_NIM_PROVIDER", None, "NIM_API_KEY"),
}


ApiTransport = Callable[[str, Mapping[str, object], str, Mapping[str, str], float], Mapping[str, object]]


class ApiPlannerProvider:
    provider_kind = "api_planner"
    transport_name = "api_chat_completions"
    supports_toolplan = True
    supports_streaming = False

    def __init__(
        self,
        config: ApiPlannerConfig,
        *,
        env: Mapping[str, str],
        confirmed: bool,
        transport: ApiTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self.model_id = (env.get(config.model_env, "").strip() or config.default_model or "")
        self._env = env
        self._confirmed = bool(confirmed)
        self._transport = transport or post_chat_completion
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._issued_smoke_plan = False
        self._metadata: dict[str, object] = {
            "provider_id": self.provider_id,
            "model_id": self.model_id or None,
            "classification": ApiPlannerClassification.DISABLED.value,
            "outbound_request_count": 0,
            "request_reached_provider": False,
            "http_status": None,
            "provider_request_id": None,
            "safe_error_code": None,
            "retry_count": 0,
            "retry_after_seconds": None,
        }

    @property
    def outbound_request_count(self) -> int:
        return int(self._metadata["outbound_request_count"])

    def to_safe_metadata(self) -> dict[str, object]:
        return dict(self._metadata)

    def request_turn(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        run_id: str,
        turn: int,
    ) -> ProductToolPlan:
        del run_id
        if turn > 1:
            if not self._issued_smoke_plan or not tool_results or tool_results[-1].get("status") != "completed":
                self._fail(ApiPlannerClassification.TOOLPLAN_UNSAFE, "tool_result_not_completed")
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "completed",
                "tool_calls": [],
                "final": True,
            })
        self._preflight(allowed_tools)
        key = self._api_key()
        instruction = _planner_instruction(smoke=_is_smoke_task(task))
        payload = _request_payload(self.config, model_id=self.model_id, instruction=instruction, task=task)
        headers: dict[str, str] = {}
        transport_key = key
        if self.config.protocol == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            transport_key = ""
        if self.provider_id == "openrouter":
            referer = self._env.get("FORGEX_OPENROUTER_HTTP_REFERER", "").strip()
            title = self._env.get("FORGEX_OPENROUTER_TITLE", "").strip()
            if referer and len(referer) <= 512 and urlparse(referer).scheme == "https":
                headers["HTTP-Referer"] = referer
            if title and len(title) <= 128:
                headers["X-Title"] = title
        self._metadata["outbound_request_count"] = 1
        try:
            for attempt in range(2):
                try:
                    response = self._transport(self.config.endpoint, payload, transport_key, headers, self._timeout_seconds)
                    self._metadata["request_reached_provider"] = True
                    break
                except ApiTransportHTTPError as exc:
                    self._metadata["retry_after_seconds"] = exc.retry_after_seconds
                    if attempt == 0 and exc.status in {429, 503} and exc.retry_after_seconds and exc.retry_after_seconds <= 10:
                        self._metadata["retry_count"] = 1
                        self._metadata["outbound_request_count"] = 2
                        time.sleep(exc.retry_after_seconds)
                        continue
                    self._map_http_error(exc)
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "provider_network_error")
        except ApiPlannerError:
            raise
        except Exception:
            self._fail(ApiPlannerClassification.UNKNOWN_SAFE_FAILURE, "provider_unknown_failure")
        content = _extract_content(response, protocol=self.config.protocol)
        try:
            plan = ProductToolPlan.parse_json(content, max_calls=5)
        except ValueError as exc:
            if str(exc) == "product_toolplan_json_invalid":
                self._fail(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_invalid_json")
            self._fail(ApiPlannerClassification.TOOLPLAN_INVALID, "provider_toolplan_schema_invalid")
        _validate_generated_plan(plan, smoke=_is_smoke_task(task))
        self._issued_smoke_plan = True
        self._metadata.update(classification=ApiPlannerClassification.READY.value, safe_error_code=None)
        return plan

    def _preflight(self, allowed_tools: Sequence[str]) -> None:
        classification = provider_readiness(self.config, self._env, confirmed=self._confirmed)
        if classification is not ApiPlannerClassification.READY:
            self._fail(classification, classification.value.casefold())
        if tuple(allowed_tools) != ("write_file",):
            self._fail(ApiPlannerClassification.TOOLPLAN_UNSAFE, "allowed_tools_invalid")
        parsed = urlparse(self.config.endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            self._fail(ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe")

    def _api_key(self) -> str:
        return self._env.get(self.config.api_key_env, "").strip() or (
            self._env.get(self.config.fallback_api_key_env, "").strip() if self.config.fallback_api_key_env else ""
        )

    def _map_http_error(self, exc: ApiTransportHTTPError) -> None:
        self._metadata.update(
            request_reached_provider=True,
            http_status=exc.status,
            provider_request_id=exc.request_id,
        )
        body = exc.safe_body.casefold()
        billing = any(marker in body for marker in ("billing", "payment", "credit", "insufficient_quota"))
        quota = any(marker in body for marker in ("quota", "resource_exhausted", "limit exceeded"))
        if exc.status == 401:
            self._fail(ApiPlannerClassification.AUTH_INVALID, "provider_auth_invalid")
        if exc.status == 403:
            self._fail(ApiPlannerClassification.BILLING_REQUIRED if billing else ApiPlannerClassification.AUTH_INVALID, "provider_forbidden")
        if exc.status == 404:
            self._fail(ApiPlannerClassification.MODEL_UNAVAILABLE, "provider_model_unavailable")
        if exc.status == 429:
            self._fail(ApiPlannerClassification.QUOTA_EXCEEDED if quota or billing else ApiPlannerClassification.RATE_LIMITED, "provider_rate_limited")
        self._fail(ApiPlannerClassification.NETWORK_ERROR, "provider_http_error")

    def _fail(self, classification: ApiPlannerClassification, safe_code: str) -> None:
        self._metadata.update(classification=classification.value, safe_error_code=safe_code)
        raise ApiPlannerError(classification, safe_code)


def provider_readiness(config: ApiPlannerConfig, env: Mapping[str, str], *, confirmed: bool) -> ApiPlannerClassification:
    globals_enabled = all(env.get(name, "").strip() == "1" for name in (
        "FORGEX_ENABLE_AGENT_RUNTIME",
        "FORGEX_ENABLE_API_PROVIDERS",
    ))
    if not globals_enabled or env.get(config.provider_flag, "").strip() != "1":
        return ApiPlannerClassification.DISABLED
    key = env.get(config.api_key_env, "").strip() or (
        env.get(config.fallback_api_key_env, "").strip() if config.fallback_api_key_env else ""
    )
    if not key:
        return ApiPlannerClassification.KEY_MISSING
    if not (env.get(config.model_env, "").strip() or config.default_model):
        return ApiPlannerClassification.MODEL_MISSING
    if not confirmed:
        return ApiPlannerClassification.CONFIRMATION_REQUIRED
    return ApiPlannerClassification.READY


def safe_provider_status(config: ApiPlannerConfig, env: Mapping[str, str], *, confirmed: bool) -> dict[str, object]:
    classification = provider_readiness(config, env, confirmed=confirmed)
    model = env.get(config.model_env, "").strip() or config.default_model or ""
    key_present = bool(env.get(config.api_key_env, "").strip() or (env.get(config.fallback_api_key_env, "").strip() if config.fallback_api_key_env else ""))
    return {
        "provider_id": config.provider_id,
        "display_name": config.display_name,
        "provider_kind": "api_planner",
        "base_url_configured": config.base_url.startswith("https://"),
        "api_key_env": config.api_key_env,
        "model_env": config.model_env,
        "default_model": config.default_model,
        "provider_flag": config.provider_flag,
        "enabled_by_default": False,
        "supports_toolplan": True,
        "supports_streaming": False,
        "provider_flag_enabled": env.get(config.provider_flag, "").strip() == "1",
        "key_present": key_present,
        "model_configured": bool(model),
        "model_id": model or None,
        "routeable": classification is ApiPlannerClassification.READY,
        "status": classification.value,
        "last_classification": None,
    }


def _planner_instruction(*, smoke: bool = False) -> str:
    if smoke:
        return (
        "You are a planner only. Return JSON only, without markdown, code fences, or prose. "
        f"Return version {PRODUCT_TOOLPLAN_VERSION} with exactly one write_file call. "
        f"Use path {API_PRODUCT_SMOKE_PATH} and exact content {API_PRODUCT_SMOKE_CONTENT}. "
        "Set final to false. Do not request shell, network, install, build, flash, apply, absolute paths, parent traversal, or hidden paths."
        )
    return (
        "You generate embedded project files only. Return JSON only using version "
        f"{PRODUCT_TOOLPLAN_VERSION}. Return one to five write_file tool_calls with complete file contents and final=false. "
        "Use workspace-relative paths only. Do not request shell, network, package installation, build, flash, monitor, apply, "
        "absolute paths, parent traversal, symlinks, hidden control files, or hardware access. Keep changes narrowly scoped to the task."
    )


def _is_smoke_task(task: str) -> bool:
    return "smoke" in task.casefold()


def _validate_generated_plan(plan: ProductToolPlan, *, smoke: bool) -> None:
    if smoke:
        _validate_exact_smoke_plan(plan)
        return
    if plan.final or not plan.tool_calls:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_toolplan_empty")
    for call in plan.tool_calls:
        if call.tool_type != "write_file" or not call.content:
            raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_requested_forbidden_tool")
        try:
            safe_relative_path(call.path)
        except ValueError as exc:
            raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_path_unsafe") from exc


def _request_payload(config: ApiPlannerConfig, *, model_id: str, instruction: str, task: str) -> dict[str, object]:
    if config.protocol == "anthropic":
        return {
            "model": model_id,
            "system": instruction,
            "messages": [{"role": "user", "content": task}],
            "temperature": 0,
            "max_tokens": 8192,
            "stream": False,
        }
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": task},
        ],
        "temperature": 0,
        "max_tokens": 8192,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def _extract_content(response: Mapping[str, object], *, protocol: str = "openai") -> str:
    try:
        if protocol == "anthropic":
            blocks = response["content"]
            if not isinstance(blocks, list):
                raise ValueError
            content = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, Mapping) and block.get("type") == "text"
            )
        else:
            choices = response["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            message = choices[0]["message"]
            content = message["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_shape_invalid") from exc
    if not isinstance(content, str) or not content or len(content.encode("utf-8")) > MAX_API_RESPONSE_BYTES:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_content_invalid")
    return content


def _validate_exact_smoke_plan(plan: ProductToolPlan) -> None:
    if plan.final or len(plan.tool_calls) != 1:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_smoke_call_count_invalid")
    call = plan.tool_calls[0]
    if call.tool_type != "write_file" or call.path != API_PRODUCT_SMOKE_PATH or call.content != API_PRODUCT_SMOKE_CONTENT:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_smoke_toolplan_unsafe")


def post_chat_completion(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    extra_headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, object]:
    if urlparse(url).scheme != "https":
        raise ApiPlannerError(ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe")
    headers = {"Content-Type": "application/json", "Accept": "application/json", **extra_headers}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=True).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read(MAX_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        request_id = exc.headers.get("x-request-id") or exc.headers.get("cf-ray")
        retry_after_raw = exc.headers.get("Retry-After")
        try:
            retry_after = int(retry_after_raw) if retry_after_raw else None
        except ValueError:
            retry_after = None
        raise ApiTransportHTTPError(exc.code, body, request_id, retry_after) from exc
    if len(data) > MAX_API_RESPONSE_BYTES:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_too_large")
    try:
        decoded: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_envelope_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_envelope_invalid")
    return decoded


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if not all(character.isalnum() or character in "._:-" for character in candidate):
        return None
    return candidate
