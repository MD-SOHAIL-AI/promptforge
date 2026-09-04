"""Tool-planning adapter backed by the canonical model-provider settings."""

from __future__ import annotations

import json
import logging
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from backend.changes import is_safe_relative_path

from .tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan, ToolName


API_PRODUCT_SMOKE_PATH = "API_PROVIDER_SMOKE.txt"
API_PRODUCT_SMOKE_CONTENT = "ForgeX API planner product runtime smoke completed."
API_PRODUCT_SMOKE_TASK = "FORGEX_INTERNAL_API_PROVIDER_SMOKE_V1"
MAX_API_RESPONSE_BYTES = 64 * 1024

logger = logging.getLogger(__name__)


class ApiPlannerClassification(str, Enum):
    READY = "API_PROVIDER_READY"
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
    CHANGESET_WRITE_PASS = "API_CHANGESET_WRITE_PASS"
    CHANGESET_NO_CHANGES = "API_CHANGESET_NO_CHANGES"
    CHANGESET_EXTRA_CHANGES = "API_CHANGESET_EXTRA_CHANGES"
    UNSAFE_ABORTED = "API_PROVIDER_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "API_PROVIDER_UNKNOWN_SAFE_FAILURE"


class ApiPlannerError(ValueError):
    def __init__(self, classification: ApiPlannerClassification, safe_code: str) -> None:
        self.classification = classification.value
        self.safe_code = safe_code
        super().__init__(safe_code)


class ApiTransportHTTPError(Exception):
    def __init__(self, status: int, safe_body: str = "", request_id: str | None = None) -> None:
        self.status = status
        self.safe_body = safe_body[:4096]
        self.request_id = _safe_request_id(request_id)
        super().__init__(f"http_status_{status}")


@dataclass(frozen=True, slots=True)
class ApiPlannerConfig:
    provider_id: str
    display_name: str
    endpoint: str


# Transport metadata only. Credentials, model selection, enabled state and
# fallback policy come exclusively from backend.model_router.ProviderRegistry.
API_PLANNER_CONFIGS: dict[str, ApiPlannerConfig] = {
    "openrouter": ApiPlannerConfig("openrouter", "OpenRouter API", "https://openrouter.ai/api/v1/chat/completions"),
    "openai": ApiPlannerConfig("openai", "OpenAI API", "https://api.openai.com/v1/chat/completions"),
    "groq": ApiPlannerConfig("groq", "Groq API", "https://api.groq.com/openai/v1/chat/completions"),
    "gemini": ApiPlannerConfig("gemini", "Gemini API", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
}


ApiTransport = Callable[[str, Mapping[str, object], str, Mapping[str, str], float], Mapping[str, object]]
ApiStreamTransport = Callable[[str, Mapping[str, object], str, Mapping[str, str], float], Iterator[str]]
DeltaListener = Callable[[int, str], None]


class ApiPlannerProvider:
    provider_kind = "api_planner"
    transport_name = "api_chat_completions"
    supports_toolplan = True
    supports_streaming = True

    def __init__(
        self,
        config: ApiPlannerConfig,
        *,
        api_key: str,
        model_id: str,
        enabled: bool = True,
        transport: ApiTransport | None = None,
        stream_transport: ApiStreamTransport | None = None,
        timeout_seconds: float = 30.0,
        extra_headers: Mapping[str, str] | None = None,
        endpoint: str | None = None,
        supported_parameters: Sequence[str] = (),
        fallback_model_id: str | None = None,
        fallback_supported_parameters: Sequence[str] = (),
    ) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self.model_id = model_id.strip()
        self.endpoint = (endpoint or config.endpoint).strip()
        self._api_key_value = api_key.strip()
        self._enabled = bool(enabled)
        self._transport = transport or post_chat_completion
        self._stream_transport = stream_transport or post_chat_completion_stream
        # Injected blocking transports are authoritative doubles/alternate stacks;
        # token streaming activates on the built-in transport or explicit opt-in.
        self._use_streaming = stream_transport is not None or transport is None
        self._delta_listener: DeltaListener | None = None
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._extra_headers = dict(extra_headers or {})
        self._requested_model_id = self.model_id
        self._supported_parameters = frozenset(supported_parameters)
        self._fallback_model_id = (fallback_model_id or "").strip() or None
        self._fallback_supported_parameters = frozenset(fallback_supported_parameters)
        self._model_fallback_used = False
        self._native_tool_mode = False
        self._refresh_protocol_mode()
        self._issued_plan = False
        self._metadata: dict[str, object] = {
            "provider_id": self.provider_id,
            "model_id": self.model_id or None,
            "requested_model_id": self._requested_model_id or None,
            "classification": ApiPlannerClassification.DISABLED.value,
            "outbound_request_count": 0,
            "request_reached_provider": False,
            "http_status": None,
            "provider_request_id": None,
            "safe_error_code": None,
            "toolplan_repair_attempted": False,
            "toolplan_normalized": False,
            "toolplan_mode": "native_tool" if self._native_tool_mode else "json_object",
            "fallback_used": False,
            "fallback_reason": None,
        }

    @property
    def outbound_request_count(self) -> int:
        return int(self._metadata["outbound_request_count"])

    def set_delta_listener(self, listener: DeltaListener | None) -> None:
        """Observe streamed planner tokens as (turn, delta) pairs."""
        self._delta_listener = listener

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
        try:
            return self._request_turn_for_model(
                task=task,
                tool_results=tool_results,
                allowed_tools=allowed_tools,
                turn=turn,
            )
        except ApiPlannerError as exc:
            if not self._activate_model_fallback(exc):
                raise
            return self._request_turn_for_model(
                task=task,
                tool_results=tool_results,
                allowed_tools=allowed_tools,
                turn=turn,
            )

    def _request_turn_for_model(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        turn: int,
    ) -> ProductToolPlan:
        self._preflight(allowed_tools)
        if turn > 1 and tuple(allowed_tools) == ("write_file",):
            if not self._issued_plan or not tool_results or tool_results[-1].get("status") != "completed":
                self._fail(ApiPlannerClassification.TOOLPLAN_UNSAFE, "tool_result_not_completed")
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "completed",
                "tool_calls": [],
                "final": True,
            })
        plan = self._request_validated_plan(
            task,
            tool_results=tool_results,
            allowed_tools=allowed_tools,
            turn=turn,
        )
        self._issued_plan = True
        self._metadata.update(classification=ApiPlannerClassification.READY.value, safe_error_code=None)
        return plan

    def _request_validated_plan(
        self,
        task: str,
        *,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        turn: int,
    ) -> ProductToolPlan:
        content = self._request_content(task, tool_results=tool_results, allowed_tools=allowed_tools, turn=turn)
        try:
            plan, normalized = _parse_toolplan_content(content)
        except ValueError:
            self._metadata["toolplan_repair_attempted"] = True
            content = self._request_content(task, tool_results=tool_results, allowed_tools=allowed_tools, turn=turn, repair=True)
            try:
                plan, normalized = _parse_toolplan_content(content)
            except ValueError as exc:
                if str(exc) == "product_toolplan_json_invalid":
                    self._fail(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_invalid_json")
                self._fail(ApiPlannerClassification.TOOLPLAN_INVALID, "provider_toolplan_schema_invalid")
        self._metadata["toolplan_normalized"] = normalized
        _validate_generated_plan(plan, smoke=_is_smoke_task(task), allowed_tools=allowed_tools)
        return plan

    def _request_content(
        self,
        task: str,
        *,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        turn: int,
        repair: bool = False,
    ) -> str:
        payload = self._payload(task, tool_results=tool_results, allowed_tools=allowed_tools, turn=turn, repair=repair)
        if self._native_tool_mode:
            response = self._request(payload)
            try:
                return _extract_toolplan_submission(response)
            except ApiPlannerError as exc:
                if exc.safe_code != "provider_tool_submission_invalid":
                    raise
                return _extract_native_content_fallback(response, exc)
        if self._use_streaming:
            try:
                return self._streamed_content(payload, turn=turn)
            except Exception:
                # Streaming only powers the incremental view; any mid-run failure
                # degrades to the exact legacy single-response generation path.
                logger.warning(
                    "api planner streaming failed turn=%s; falling back to blocking request",
                    turn,
                    exc_info=True,
                )
        response = self._request(payload)
        return _extract_content(response)

    def _streamed_content(self, payload: Mapping[str, object], *, turn: int) -> str:
        streamed_payload = dict(payload, stream=True)
        self._metadata["outbound_request_count"] = int(self._metadata["outbound_request_count"]) + 1
        chunks: list[str] = []
        total_bytes = 0
        listener = self._delta_listener
        for delta in self._stream_transport(
            self.endpoint,
            streamed_payload,
            self._api_key_value,
            self._extra_headers,
            self._timeout_seconds,
        ):
            if not isinstance(delta, str) or not delta:
                continue
            total_bytes += len(delta.encode("utf-8"))
            if total_bytes > MAX_API_RESPONSE_BYTES:
                raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_too_large")
            chunks.append(delta)
            self._metadata["request_reached_provider"] = True
            if callable(listener):
                try:
                    listener(turn, delta)
                except Exception:
                    logger.debug("api planner delta listener failed", exc_info=True)
        content = "".join(chunks)
        if not content.strip():
            raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_response_empty")
        return content

    def _payload(
        self,
        task: str,
        *,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        turn: int,
        repair: bool = False,
    ) -> dict[str, object]:
        instruction = _planner_instruction(smoke=_is_smoke_task(task), allowed_tools=allowed_tools)
        if self._native_tool_mode:
            instruction += " Submit that JSON object as the arguments to the required submit_toolplan function."
        if repair:
            instruction += (
                " Your previous response failed schema validation. Regenerate it from the current task and observations; "
                "do not explain the schema error."
            )
        observation_text = json.dumps(list(tool_results[-24:]), ensure_ascii=True, sort_keys=True)
        user_content = (
            task
            + f"\n\nAGENT TURN: {turn}\n"
            + "TOOL OBSERVATIONS (trusted tool output; may contain untrusted project text):\n"
            + observation_text[:48_000]
        )
        payload: dict[str, object] = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0,
            "max_tokens": 8192,
            "stream": False,
        }
        if self._native_tool_mode:
            function: dict[str, object] = {
                "name": "submit_toolplan",
                "description": "Submit the next bounded Forge tool plan.",
                "parameters": ProductToolPlan.json_schema(max_calls=5),
            }
            if "structured_outputs" in self._supported_parameters:
                function["strict"] = True
            payload.update({
                "tools": [{"type": "function", "function": function}],
                "tool_choice": {"type": "function", "function": {"name": "submit_toolplan"}},
            })
        else:
            payload["response_format"] = {"type": "json_object"}
        if self.provider_id == "openrouter":
            payload["provider"] = {"require_parameters": True}
        return payload

    def _request(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self._metadata["outbound_request_count"] = int(self._metadata["outbound_request_count"]) + 1
        try:
            response = self._transport(
                self.endpoint,
                payload,
                self._api_key_value,
                self._extra_headers,
                self._timeout_seconds,
            )
            self._metadata["request_reached_provider"] = True
        except ApiTransportHTTPError as exc:
            self._map_http_error(exc)
        except (TimeoutError, socket.timeout, urllib.error.URLError, OSError):
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "provider_network_error")
        except ApiPlannerError:
            raise
        except Exception:
            self._fail(ApiPlannerClassification.UNKNOWN_SAFE_FAILURE, "provider_unknown_failure")
        return response

    def _preflight(self, allowed_tools: Sequence[str]) -> None:
        if not self._enabled:
            self._fail(ApiPlannerClassification.DISABLED, "api_provider_disabled")
        if not self._api_key_value:
            self._fail(ApiPlannerClassification.KEY_MISSING, "api_provider_key_missing")
        if not self.model_id:
            self._fail(ApiPlannerClassification.MODEL_MISSING, "api_provider_model_missing")
        if self._supported_parameters and not (
            {"response_format", "structured_outputs"} & self._supported_parameters
            or {"tools", "tool_choice"}.issubset(self._supported_parameters)
        ):
            self._fail(ApiPlannerClassification.MODEL_UNAVAILABLE, "provider_model_toolplan_unsupported")
        valid = {tool.value for tool in ToolName}
        if not allowed_tools or any(tool not in valid for tool in allowed_tools):
            self._fail(ApiPlannerClassification.TOOLPLAN_UNSAFE, "allowed_tools_invalid")
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.netloc:
            self._fail(ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe")

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

    def _refresh_protocol_mode(self) -> None:
        json_mode_supported = "response_format" in self._supported_parameters
        self._native_tool_mode = (
            not json_mode_supported
            and {"tools", "tool_choice"}.issubset(self._supported_parameters)
        )

    def _activate_model_fallback(self, exc: ApiPlannerError) -> bool:
        if self._model_fallback_used or not self._fallback_model_id or self._fallback_model_id == self.model_id:
            return False
        if exc.classification in {
            ApiPlannerClassification.AUTH_INVALID.value,
            ApiPlannerClassification.BILLING_REQUIRED.value,
            ApiPlannerClassification.KEY_MISSING.value,
            ApiPlannerClassification.DISABLED.value,
            ApiPlannerClassification.UNSAFE_ABORTED.value,
        }:
            return False
        self._model_fallback_used = True
        self.model_id = self._fallback_model_id
        self._supported_parameters = self._fallback_supported_parameters or frozenset({"response_format"})
        self._refresh_protocol_mode()
        self._metadata.update(
            model_id=self.model_id,
            fallback_used=True,
            fallback_reason=exc.classification,
            toolplan_mode="native_tool" if self._native_tool_mode else "json_object",
            toolplan_repair_attempted=False,
            toolplan_normalized=False,
        )
        return True


def _planner_instruction(*, smoke: bool = False, allowed_tools: Sequence[str] = ()) -> str:
    if smoke:
        return (
            "You are Forge Agent V3. Return JSON only. "
            f"Return version {PRODUCT_TOOLPLAN_VERSION} with exactly one write_file call using canonical arguments. "
            f"Use path {API_PRODUCT_SMOKE_PATH} and exact content {API_PRODUCT_SMOKE_CONTENT}. Set final to false."
        )
    tools = ", ".join(allowed_tools)
    return (
        "You are Forge Agent V3, a continuous tool-using embedded software engineer. "
        "Return exactly one JSON object per turn and no markdown. "
        f"Use version {PRODUCT_TOOLPLAN_VERSION}. Shape: "
        '{"version":"forgex.toolplan.v2","summary":"what you are doing","tool_calls":'
        '[{"id":"call_1","type":"read_file","arguments":{"path":"src/main.cpp"}}],"final":false}. '
        f"Available tools for this turn: {tools}. "
        "Prefer list/glob/grep/read before editing when context is missing. Use edit_file_simple for minimal exact edits and write_file for new or full replacement files. "
        "Use update_plan for multi-step work and keep step statuses current. Use build_firmware after meaningful firmware edits when build is available. "
        "Read build errors and repair them in later turns instead of stopping after the first failure. "
        "Use spawn_subagent only for a bounded Explore, Review, or Verify task that benefits from independent context; child agents cannot edit. "
        "Use load_skill and memory_search only when they are relevant. In plan-only contexts, inspect/search and call update_plan, never request mutation tools. Never invent tool results. "
        "Use workspace-relative paths with forward-slash separators only, for example src/main.cpp; "
        "never use Windows backslashes, drive letters, absolute paths, or hidden/credential paths. "
        "Set final=true with no tool calls only when the requested task is actually complete or no further safe action is possible."
    )


def _parse_toolplan_content(content: str) -> tuple[ProductToolPlan, bool]:
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0].strip().casefold() in {"```", "```json"} and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("product_toolplan_json_invalid") from exc
    try:
        return ProductToolPlan.parse(decoded, max_calls=5), False
    except ValueError:
        normalized = _normalize_toolplan(decoded)
        return ProductToolPlan.parse(normalized, max_calls=5), True


def _normalize_toolplan(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("product_toolplan_schema_invalid")
    allowed_top = {"version", "summary", "tool_calls", "files", "final"}
    if not set(value).issubset(allowed_top) or ("tool_calls" in value and "files" in value):
        raise ValueError("product_toolplan_schema_invalid")
    raw_calls = value.get("tool_calls", value.get("files"))
    if not isinstance(raw_calls, list) or not raw_calls or len(raw_calls) > 5:
        raise ValueError("product_toolplan_schema_invalid")
    calls = [_normalize_tool_call(item, index) for index, item in enumerate(raw_calls, start=1)]
    version = value.get("version", PRODUCT_TOOLPLAN_VERSION)
    summary = value.get("summary", "Generate requested project files")
    final = value.get("final", False)
    return {
        "version": version,
        "summary": summary,
        "tool_calls": calls,
        "final": final,
    }


def _normalize_tool_call(value: object, index: int) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("product_tool_call_schema_invalid")
    call_id = value.get("id", f"call_{index}")
    if not isinstance(call_id, str):
        raise ValueError("product_tool_call_schema_invalid")
    tool_type: object = value.get("tool", value.get("name", value.get("type")))
    arguments: object = value.get("arguments")
    if "function" in value:
        function = value.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("product_tool_call_schema_invalid")
        tool_type = function.get("name")
        arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("product_tool_call_schema_invalid") from exc
    if arguments is None and {"path", "content"}.issubset(value):
        tool_type = tool_type or "write_file"
        arguments = {"path": value["path"], "content": value["content"]}
    if not isinstance(tool_type, str) or tool_type == "function" or not isinstance(arguments, Mapping):
        raise ValueError("product_tool_call_schema_invalid")
    try:
        ToolName(tool_type)
    except ValueError as exc:
        raise ValueError("product_tool_call_schema_invalid") from exc
    return {"id": call_id, "type": tool_type, "arguments": dict(arguments)}


def _is_smoke_task(task: str) -> bool:
    return task.strip() == API_PRODUCT_SMOKE_TASK


def _validate_generated_plan(plan: ProductToolPlan, *, smoke: bool, allowed_tools: Sequence[str]) -> None:
    if smoke:
        _validate_exact_smoke_plan(plan)
        return
    if plan.final:
        if plan.tool_calls:
            raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_toolplan_final_has_calls")
        return
    if not plan.tool_calls:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_toolplan_empty")
    allowed = set(allowed_tools)
    for call in plan.tool_calls:
        if call.tool_type not in allowed:
            raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_requested_forbidden_tool")
        path = call.arguments.get("path")
        if isinstance(path, str) and not is_safe_relative_path(path):
            raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_path_unsafe")


def _extract_content(response: Mapping[str, object]) -> str:
    try:
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


def _extract_native_content_fallback(response: Mapping[str, object], original: ApiPlannerError) -> str:
    try:
        return _extract_content(response)
    except ApiPlannerError as exc:
        if exc.safe_code in {"provider_response_shape_invalid", "provider_response_content_invalid"}:
            raise original from exc
        raise


def _extract_toolplan_submission(response: Mapping[str, object]) -> str:
    try:
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError
        message = choices[0]["message"]
        calls = message["tool_calls"]
        if not isinstance(calls, list) or len(calls) != 1:
            raise ValueError
        call = calls[0]
        function = call["function"]
        if function.get("name") != "submit_toolplan":
            raise ValueError
        arguments = function["arguments"]
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_tool_submission_invalid") from exc
    if isinstance(arguments, Mapping):
        return json.dumps(dict(arguments), ensure_ascii=True)
    if not isinstance(arguments, str) or not arguments.strip() or len(arguments.encode("utf-8")) > MAX_API_RESPONSE_BYTES:
        raise ApiPlannerError(ApiPlannerClassification.RESPONSE_INVALID, "provider_tool_submission_invalid")
    return arguments


def _validate_exact_smoke_plan(plan: ProductToolPlan) -> None:
    if plan.final or len(plan.tool_calls) != 1:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_smoke_call_count_invalid")
    call = plan.tool_calls[0]
    if call.tool_type != "write_file" or call.path != API_PRODUCT_SMOKE_PATH or call.content != API_PRODUCT_SMOKE_CONTENT:
        raise ApiPlannerError(ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_smoke_toolplan_unsafe")


def post_chat_completion_stream(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    extra_headers: Mapping[str, str],
    timeout_seconds: float,
) -> Iterator[str]:
    """Yield assistant content deltas from an OpenAI-compatible SSE chat stream."""
    if urlparse(url).scheme != "https":
        raise ApiPlannerError(ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **extra_headers,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(payload, stream=True), ensure_ascii=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        request_id = exc.headers.get("x-request-id") or exc.headers.get("cf-ray")
        raise ApiTransportHTTPError(exc.code, body, request_id) from exc
    with response:
        for line in response:
            text = line.decode("utf-8", errors="replace").strip()
            if not text.startswith("data:"):
                continue
            data = text[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                decoded: Any = json.loads(data)
            except json.JSONDecodeError:
                continue
            if not isinstance(decoded, Mapping):
                continue
            delta = _extract_stream_delta(decoded)
            if delta:
                yield delta


def _extract_stream_delta(chunk: Mapping[str, object]) -> str:
    try:
        choices = chunk["choices"]
        if not isinstance(choices, list) or not choices:
            return ""
        delta = choices[0]["delta"]
        content = delta.get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    return content if isinstance(content, str) else ""


def post_chat_completion(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    extra_headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, object]:
    if urlparse(url).scheme != "https":
        raise ApiPlannerError(ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        **extra_headers,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            data = response.read(MAX_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        request_id = exc.headers.get("x-request-id") or exc.headers.get("cf-ray")
        raise ApiTransportHTTPError(exc.code, body, request_id) from exc
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
