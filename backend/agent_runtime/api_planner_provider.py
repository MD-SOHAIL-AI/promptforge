"""Tool-planning adapter backed by the canonical model-provider settings."""

from __future__ import annotations

import json
import re
import socket
import time
import uuid
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlparse

from backend.changes import is_safe_relative_path
from backend.model_router.models import ModelCapabilities, ModelResponse
from backend.model_router.normalization import (
    ProviderNormalizationError,
    normalize_response,
    normalize_stream_event,
    response_payload,
)
from backend.observability.diagnostics import emit_diagnostic_event

from .tool_contracts import (
    PRODUCT_TOOLPLAN_VERSION,
    ProductToolCall,
    ProductToolPlan,
    ToolName,
    WRITE_TOOLS,
    parse_tool_name,
    provider_function_schema,
)

API_PRODUCT_SMOKE_PATH = "API_PROVIDER_SMOKE.txt"
API_PRODUCT_SMOKE_CONTENT = "ForgeX API planner product runtime smoke completed."
MAX_API_RESPONSE_BYTES = 64 * 1024


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
    CONTENT_MISSING = "API_CONTENT_MISSING"
    OUTPUT_TRUNCATED = "API_OUTPUT_TRUNCATED"
    OUTPUT_NOT_JSON = "API_OUTPUT_NOT_JSON"
    TOOLPLAN_INVALID = "API_TOOLPLAN_INVALID"
    TOOLPLAN_UNSAFE = "API_TOOLPLAN_UNSAFE"
    CHANGESET_WRITE_PASS = "API_CHANGESET_WRITE_PASS"
    CHANGESET_NO_CHANGES = "API_CHANGESET_NO_CHANGES"
    CHANGESET_EXTRA_CHANGES = "API_CHANGESET_EXTRA_CHANGES"
    UNSAFE_ABORTED = "API_PROVIDER_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "API_PROVIDER_UNKNOWN_SAFE_FAILURE"


class ApiPlannerError(ValueError):
    def __init__(
        self,
        classification: ApiPlannerClassification,
        safe_code: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.classification = classification.value
        self.safe_code = safe_code
        self.details = dict(details or {})
        super().__init__(safe_code)


class ApiTransportHTTPError(Exception):
    def __init__(
        self, status: int, safe_body: str = "", request_id: str | None = None
    ) -> None:
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
    "openrouter": ApiPlannerConfig(
        "openrouter", "OpenRouter API", "https://openrouter.ai/api/v1/chat/completions"
    ),
    "openai": ApiPlannerConfig(
        "openai", "OpenAI API", "https://api.openai.com/v1/chat/completions"
    ),
    "groq": ApiPlannerConfig(
        "groq", "Groq API", "https://api.groq.com/openai/v1/chat/completions"
    ),
    "nvidia_nim": ApiPlannerConfig(
        "nvidia_nim",
        "NVIDIA NIM",
        "https://integrate.api.nvidia.com/v1/chat/completions",
    ),
    "gemini": ApiPlannerConfig(
        "gemini",
        "Gemini API",
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    ),
}


ApiTransport = Callable[
    [str, Mapping[str, object], str, Mapping[str, str], float], Mapping[str, object]
]


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
        timeout_seconds: float = 30.0,
        extra_headers: Mapping[str, str] | None = None,
        endpoint: str | None = None,
        fallback_model_ids: Sequence[str] | None = None,
        capabilities_by_model: Mapping[str, ModelCapabilities] | None = None,
    ) -> None:
        self.config = config
        self.provider_id = config.provider_id
        self.model_id = model_id.strip()
        self.endpoint = (endpoint or config.endpoint).strip()
        self._api_key_value = api_key.strip()
        self._enabled = bool(enabled)
        self._uses_default_transport = transport is None
        self._transport = transport or post_chat_completion
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._extra_headers = dict(extra_headers or {})
        # Recovery is deliberately bounded: one selected model and at most one
        # alternate model, with one schema-repair request shared by the turn.
        self._fallback_model_ids = tuple(
            dict.fromkeys(
                candidate.strip()
                for candidate in (fallback_model_ids or ())
                if isinstance(candidate, str)
                and candidate.strip()
                and candidate.strip() != self.model_id
            )
        )[:1]
        self._capabilities_by_model = dict(capabilities_by_model or {})
        self._issued_plan = False
        self._metadata: dict[str, object] = {
            "provider_id": self.provider_id,
            "model_id": self.model_id or None,
            "selected_model_id": self.model_id or None,
            "fallback_model_ids": self._fallback_model_ids,
            "fallback_used": False,
            "classification": ApiPlannerClassification.DISABLED.value,
            "outbound_request_count": 0,
            "request_reached_provider": False,
            "http_status": None,
            "provider_request_id": None,
            "safe_error_code": None,
            "toolplan_repair_attempted": False,
            "toolplan_normalized": False,
            "streaming_used": False,
            "stream_delta_count": 0,
            "structured_output_mode": self._structured_output_mode(),
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
        return self._request_turn_impl(
            task=task,
            tool_results=tool_results,
            allowed_tools=allowed_tools,
            run_id=run_id,
            turn=turn,
            stream=False,
            on_summary=None,
        )

    def request_turn_stream(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        run_id: str,
        turn: int,
        on_summary: Callable[[str], None] | None = None,
    ) -> ProductToolPlan:
        """Consume an OpenAI-compatible SSE response while exposing only the safe summary field.

        The raw streamed ToolPlan JSON is never sent to the UI.  Forge incrementally
        extracts the model-provided ``summary`` string, while the complete JSON is
        still validated before any tool can execute.  Custom test transports fall
        back to the non-streaming contract for backwards compatibility.
        """
        return self._request_turn_impl(
            task=task,
            tool_results=tool_results,
            allowed_tools=allowed_tools,
            run_id=run_id,
            turn=turn,
            stream=self._uses_default_transport,
            on_summary=on_summary,
        )

    def _request_turn_impl(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        run_id: str,
        turn: int,
        stream: bool,
        on_summary: Callable[[str], None] | None,
    ) -> ProductToolPlan:
        turn_id = f"{run_id}:turn-{turn}"
        self._preflight(allowed_tools)
        if turn > 1 and tuple(allowed_tools) == ("write_file",):
            if (
                not self._issued_plan
                or not tool_results
                or tool_results[-1].get("status") != "completed"
            ):
                self._fail(
                    ApiPlannerClassification.TOOLPLAN_UNSAFE,
                    "tool_result_not_completed",
                )
            return ProductToolPlan.parse(
                {
                    "version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": "completed",
                    "tool_calls": [],
                    "final": True,
                }
            )
        # Native function arguments require indexed delta assembly. Until a
        # provider advertises JSON-schema streaming, request them atomically.
        request_stream = (
            stream
            and self._uses_default_transport
            and self._capabilities().streaming
            and self._structured_output_mode() != "native_tools"
        )
        failures: list[ApiPlannerError] = []
        repair_attempted = False
        for model_id in self._model_attempt_ids():
            self.model_id = model_id
            self._metadata["model_id"] = model_id
            self._metadata["structured_output_mode"] = self._structured_output_mode()
            payload = self._payload(
                task,
                tool_results=tool_results,
                allowed_tools=allowed_tools,
                turn=turn,
                stream=request_stream,
            )
            try:
                response = (
                    self._request_stream_once(
                        payload, on_summary=on_summary, run_id=run_id, turn_id=turn_id
                    )
                    if request_stream
                    else self._request_once(payload, run_id=run_id, turn_id=turn_id)
                )
                content = self._canonical_plan_payload(response)
                try:
                    plan, normalized = _parse_toolplan_content(content)
                except ValueError as exc:
                    if repair_attempted:
                        raise _toolplan_parse_error(exc) from exc
                    repair_attempted = True
                    self._metadata["toolplan_repair_attempted"] = True
                    repair_payload = self._payload(
                        task,
                        tool_results=tool_results,
                        allowed_tools=allowed_tools,
                        turn=turn,
                        repair=True,
                        stream=request_stream,
                    )
                    response = (
                        self._request_stream_once(
                            repair_payload,
                            on_summary=on_summary,
                            run_id=run_id,
                            turn_id=turn_id,
                        )
                        if request_stream
                        else self._request_once(
                            repair_payload, run_id=run_id, turn_id=turn_id
                        )
                    )
                    content = self._canonical_plan_payload(response)
                    try:
                        plan, normalized = _parse_toolplan_content(content)
                    except ValueError as exc:
                        raise _toolplan_parse_error(exc) from exc
                self._metadata["toolplan_normalized"] = normalized
                try:
                    _validate_generated_plan(
                        plan,
                        smoke=_is_smoke_task(task),
                        allowed_tools=allowed_tools,
                        task=task,
                        tool_results=tool_results,
                        turn=turn,
                    )
                except ApiPlannerError as exc:
                    self._metadata.update(
                        classification=exc.classification, safe_error_code=exc.safe_code
                    )
                    self._emit_toolplan_rejected(exc, run_id=run_id, turn_id=turn_id)
                    raise
                self._issued_plan = True
                self._metadata.update(
                    classification=ApiPlannerClassification.READY.value,
                    safe_error_code=None,
                )
                if model_id != self._metadata.get("selected_model_id"):
                    self._metadata.update(
                        fallback_used=True, fallback_model_id=model_id
                    )
                if on_summary is not None and plan.summary:
                    on_summary(plan.summary)
                return plan
            except ApiPlannerError as exc:
                self._metadata.update(
                    classification=exc.classification, safe_error_code=exc.safe_code
                )
                failures.append(exc)
                if self._should_retry_model_fallback(model_id, exc):
                    continue
                raise
        if failures:
            raise failures[-1]
        raise AssertionError("unreachable")

    def _payload(
        self,
        task: str,
        *,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        turn: int,
        repair: bool = False,
        stream: bool = False,
    ) -> dict[str, object]:
        instruction = _planner_instruction(
            smoke=_is_smoke_task(task), allowed_tools=allowed_tools
        )
        if repair:
            instruction += (
                " Your previous response failed schema validation. Regenerate it from the current task and observations; "
                "do not explain the schema error."
            )
        observation_text = json.dumps(
            list(tool_results[-24:]), ensure_ascii=True, sort_keys=True
        )
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
            # Plans can span multiple turns. Keeping each turn compact prevents
            # reasoning-heavy models from filling 8K tokens and timing out only
            # to finish with a provider `length` stop.
            "max_tokens": 4096,
            "stream": bool(stream),
        }
        capabilities = self._capabilities()
        schema = ProductToolPlan.json_schema(max_calls=5)
        if capabilities.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "forgex_tool_plan",
                    "strict": True,
                    "schema": schema,
                },
            }
        elif capabilities.native_tools:
            native_tools = []
            for name in allowed_tools:
                try:
                    tool = parse_tool_name(str(name))
                except ValueError:
                    continue
                native_tools.append(provider_function_schema(tool))
            if native_tools:
                payload["tools"] = native_tools
                # Auto lets a capable model either call tools or finish with a
                # user-facing answer.  ForgeX still validates every call after
                # the provider response settles.
                payload["tool_choice"] = "auto"
        return payload

    def _capabilities(self) -> ModelCapabilities:
        return self._capabilities_by_model.get(self.model_id, ModelCapabilities())

    def _structured_output_mode(self) -> str:
        capabilities = self._capabilities()
        if capabilities.json_schema:
            return "json_schema"
        if capabilities.native_tools:
            return "native_tools"
        return "prompted_json"

    def _canonical_plan_payload(self, response: ModelResponse) -> object:
        """Select planner data from the canonical provider contract only."""

        if response.truncated:
            raise ApiPlannerError(
                ApiPlannerClassification.OUTPUT_TRUNCATED,
                "MODEL_OUTPUT_TRUNCATED",
            )
        if self._structured_output_mode() == "native_tools":
            if response.tool_calls:
                # Compatibility: older ForgeX adapters exposed one
                # submit_tool_plan wrapper. Accept it explicitly while new
                # providers receive per-tool schemas.
                first = response.tool_calls[0] if len(response.tool_calls) == 1 else None
                if isinstance(first, Mapping) and str(first.get("name") or "") == "submit_tool_plan":
                    arguments = first.get("arguments")
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except json.JSONDecodeError as exc:
                            raise ApiPlannerError(
                                ApiPlannerClassification.TOOLPLAN_INVALID,
                                "TOOLPLAN_SCHEMA_INVALID",
                            ) from exc
                    if not isinstance(arguments, Mapping):
                        raise ApiPlannerError(
                            ApiPlannerClassification.TOOLPLAN_INVALID,
                            "TOOLPLAN_SCHEMA_INVALID",
                        )
                    payload = dict(arguments)
                else:
                    calls = [_normalize_tool_call(call, index) for index, call in enumerate(response.tool_calls)]
                    payload = {
                        "version": PRODUCT_TOOLPLAN_VERSION,
                        "summary": (response.text or "Using ForgeX tools.")[:512],
                        "tool_calls": calls,
                        "final": False,
                    }
            else:
                text = (response.text or "").strip()
                if not text:
                    raise ApiPlannerError(
                        ApiPlannerClassification.CONTENT_MISSING,
                        "PROVIDER_CONTENT_MISSING",
                    )
                payload = {
                    "version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": text[:16000],
                    "tool_calls": [],
                    "final": True,
                }
        else:
            payload = response_payload(response)
        if payload is None:
            raise ApiPlannerError(
                ApiPlannerClassification.CONTENT_MISSING,
                "PROVIDER_CONTENT_MISSING",
            )
        try:
            size = len(
                (
                    payload
                    if isinstance(payload, str)
                    else json.dumps(payload, ensure_ascii=True)
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ApiPlannerError(
                ApiPlannerClassification.RESPONSE_INVALID,
                "FORGEX_NORMALIZATION_ERROR",
            ) from exc
        if size > MAX_API_RESPONSE_BYTES:
            raise ApiPlannerError(
                ApiPlannerClassification.RESPONSE_INVALID,
                "PROVIDER_RESPONSE_TOO_LARGE",
            )
        return payload

    def _normalize_provider_response(
        self, response: Mapping[str, object]
    ) -> ModelResponse:
        """Provider boundary: raw envelopes never escape this method."""

        try:
            normalized = normalize_response(
                response,
                provider_id=self.provider_id,
                model_id=self.model_id,
            )
        except ProviderNormalizationError as exc:
            classification = {
                "MODEL_OUTPUT_EMPTY": ApiPlannerClassification.CONTENT_MISSING,
                "MODEL_OUTPUT_TRUNCATED": ApiPlannerClassification.OUTPUT_TRUNCATED,
            }.get(exc.code, ApiPlannerClassification.RESPONSE_INVALID)
            raise ApiPlannerError(classification, exc.code) from exc
        return normalized.to_response(
            provider_id=self.provider_id, model_id=self.model_id
        )

    def _request(
        self, payload: Mapping[str, object], *, run_id: str, turn_id: str
    ) -> ModelResponse:
        failures: list[ApiPlannerError] = []
        for model_id in self._model_attempt_ids():
            attempt_payload = dict(payload)
            attempt_payload["model"] = model_id
            self.model_id = model_id
            self._metadata["model_id"] = model_id
            try:
                response = self._request_once(
                    attempt_payload, run_id=run_id, turn_id=turn_id
                )
                if model_id != self._metadata.get("selected_model_id"):
                    self._metadata.update(
                        fallback_used=True, fallback_model_id=model_id
                    )
                return response
            except ApiPlannerError as exc:
                failures.append(exc)
                if not self._should_retry_model_fallback(model_id, exc):
                    raise
        if failures:
            raise failures[-1]
        raise AssertionError("unreachable")

    def _request_once(
        self, payload: Mapping[str, object], *, run_id: str, turn_id: str
    ) -> ModelResponse:
        self._metadata["outbound_request_count"] = (
            int(self._metadata["outbound_request_count"]) + 1
        )
        request_id = f"provider-{uuid.uuid4().hex}"
        started = time.monotonic()
        self._emit_provider_event(
            "provider.request.started",
            request_id=request_id,
            run_id=run_id,
            turn_id=turn_id,
        )
        try:
            raw_response = self._transport(
                self.endpoint,
                payload,
                self._api_key_value,
                self._extra_headers,
                self._timeout_seconds,
            )
            self._metadata["request_reached_provider"] = True
            response = self._normalize_provider_response(raw_response)
            self._emit_provider_event(
                "provider.request.completed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                response=response,
                status=200,
            )
        except ApiTransportHTTPError as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                status=exc.status,
                error_code=f"http_status_{exc.status}",
                message=_safe_provider_error_message(exc.safe_body, exc.status),
                provider_request_id=exc.request_id,
            )
            self._map_http_error(exc)
        except (TimeoutError, socket.timeout) as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code="PROVIDER_TIMEOUT",
                message=str(exc)[:1024],
            )
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_TIMEOUT")
        except (urllib.error.URLError, OSError) as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code="PROVIDER_UNAVAILABLE",
                message=str(exc)[:1024],
            )
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_UNAVAILABLE")
        except ApiPlannerError as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code=exc.safe_code,
                message=exc.safe_code,
            )
            raise
        except Exception as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code=type(exc).__name__,
                message="provider_unknown_failure",
            )
            self._fail(
                ApiPlannerClassification.UNKNOWN_SAFE_FAILURE,
                "provider_unknown_failure",
            )
        return response

    def _request_stream(
        self,
        payload: Mapping[str, object],
        *,
        on_summary: Callable[[str], None] | None,
        run_id: str,
        turn_id: str,
    ) -> ModelResponse:
        if not self._uses_default_transport:
            fallback = dict(payload)
            fallback["stream"] = False
            return self._request(fallback, run_id=run_id, turn_id=turn_id)
        failures: list[ApiPlannerError] = []
        for model_id in self._model_attempt_ids():
            attempt_payload = dict(payload)
            attempt_payload["model"] = model_id
            self.model_id = model_id
            self._metadata["model_id"] = model_id
            try:
                response = self._request_stream_once(
                    attempt_payload,
                    on_summary=on_summary,
                    run_id=run_id,
                    turn_id=turn_id,
                )
                if model_id != self._metadata.get("selected_model_id"):
                    self._metadata.update(
                        fallback_used=True, fallback_model_id=model_id
                    )
                return response
            except ApiPlannerError as exc:
                failures.append(exc)
                if not self._should_retry_model_fallback(model_id, exc):
                    raise
        if failures:
            raise failures[-1]
        raise AssertionError("unreachable")

    def _request_stream_once(
        self,
        payload: Mapping[str, object],
        *,
        on_summary: Callable[[str], None] | None,
        run_id: str,
        turn_id: str,
    ) -> ModelResponse:
        self._metadata["outbound_request_count"] = (
            int(self._metadata["outbound_request_count"]) + 1
        )
        self._metadata["streaming_used"] = True
        request_id = f"provider-{uuid.uuid4().hex}"
        started = time.monotonic()
        self._emit_provider_event(
            "provider.request.started",
            request_id=request_id,
            run_id=run_id,
            turn_id=turn_id,
            stream=True,
        )
        last_summary = ""

        def content_delta(delta: str, full_content: str) -> None:
            nonlocal last_summary
            self._metadata["stream_delta_count"] = (
                int(self._metadata["stream_delta_count"]) + 1
            )
            summary = _partial_json_string_value(full_content, "summary")
            if on_summary is not None and summary and summary != last_summary:
                last_summary = summary
                on_summary(summary)

        try:
            raw_response = stream_chat_completion(
                self.endpoint,
                payload,
                self._api_key_value,
                self._extra_headers,
                self._timeout_seconds,
                content_delta,
            )
            self._metadata["request_reached_provider"] = True
            response = self._normalize_provider_response(raw_response)
            self._emit_provider_event(
                "provider.request.completed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                response=response,
                status=200,
                stream=True,
            )
            return response
        except ApiTransportHTTPError as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                status=exc.status,
                error_code=f"http_status_{exc.status}",
                message=_safe_provider_error_message(exc.safe_body, exc.status),
                provider_request_id=exc.request_id,
                stream=True,
            )
            self._map_http_error(exc)
        except (TimeoutError, socket.timeout) as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code="PROVIDER_TIMEOUT",
                message=str(exc)[:1024],
                stream=True,
            )
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_TIMEOUT")
        except (urllib.error.URLError, OSError) as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code="PROVIDER_UNAVAILABLE",
                message=str(exc)[:1024],
                stream=True,
            )
            self._fail(ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_UNAVAILABLE")
        except ApiPlannerError as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code=exc.safe_code,
                message=exc.safe_code,
                stream=True,
            )
            raise
        except Exception as exc:
            self._emit_provider_event(
                "provider.request.failed",
                request_id=request_id,
                run_id=run_id,
                turn_id=turn_id,
                duration_ms=_elapsed_ms(started),
                error_code=type(exc).__name__,
                message="provider_unknown_failure",
                stream=True,
            )
            self._fail(
                ApiPlannerClassification.UNKNOWN_SAFE_FAILURE,
                "provider_unknown_failure",
            )
        raise AssertionError("unreachable")

    def _model_attempt_ids(self) -> tuple[str, ...]:
        if not self._fallback_model_ids:
            return (self.model_id,)
        primary = str(self._metadata.get("selected_model_id") or self.model_id)
        return (
            primary,
            *tuple(
                model_id for model_id in self._fallback_model_ids if model_id != primary
            ),
        )

    def _should_retry_model_fallback(self, model_id: str, exc: ApiPlannerError) -> bool:
        if (
            self.provider_id != "openrouter"
            or not model_id.endswith(":free")
            or not self._fallback_model_ids
        ):
            return False
        if exc.safe_code in {
            "provider_model_unavailable",
            "provider_rate_limited",
            "provider_http_error",
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_RATE_LIMITED",
            "PROVIDER_HTTP_ERROR",
            "PROVIDER_TIMEOUT",
        }:
            return True
        if exc.safe_code in {
            "provider_toolplan_schema_invalid",
            "provider_response_invalid_json",
            "MODEL_OUTPUT_NOT_JSON",
            "MODEL_OUTPUT_TRUNCATED",
            "TOOLPLAN_SCHEMA_INVALID",
            "TOOLPLAN_VERSION_INVALID",
            "TOOLPLAN_TOOL_UNKNOWN",
            "TOOL_ARGUMENTS_INVALID",
        }:
            return True
        status = self._metadata.get("http_status")
        if status in {404, 429}:
            return True
        return False

    def _emit_toolplan_rejected(
        self, exc: ApiPlannerError, *, run_id: str, turn_id: str
    ) -> None:
        fields: dict[str, object] = {
            "run_id": run_id,
            "turn_id": turn_id,
            "provider": self.provider_id,
            "model": self.model_id,
            "status": "rejected",
            "error_code": exc.safe_code,
        }
        fields.update(exc.details)
        emit_diagnostic_event(
            component="provider",
            event="provider.toolplan.rejected",
            level="ERROR",
            message=exc.safe_code,
            **fields,
        )

    def _emit_provider_event(
        self,
        event: str,
        *,
        request_id: str,
        run_id: str,
        turn_id: str,
        duration_ms: int | None = None,
        response: ModelResponse | None = None,
        status: int | None = None,
        error_code: str | None = None,
        message: str | None = None,
        provider_request_id: str | None = None,
        stream: bool = False,
    ) -> None:
        fields: dict[str, object] = {
            "request_id": request_id,
            "run_id": run_id,
            "turn_id": turn_id,
            "provider": self.provider_id,
            "model": self.model_id,
            "stream": stream,
        }
        if duration_ms is not None:
            fields["duration_ms"] = duration_ms
        if status is not None:
            fields["status"] = status
        if error_code is not None:
            fields["error_code"] = error_code
        if provider_request_id:
            fields["provider_request_id"] = provider_request_id
        if response is not None:
            fields.update(_provider_response_summary(response))
        emit_diagnostic_event(
            component="provider",
            event=event,
            level="ERROR" if event.endswith(".failed") else "INFO",
            message=message,
            **fields,
        )

    def _preflight(self, allowed_tools: Sequence[str]) -> None:
        if not self._enabled:
            self._fail(ApiPlannerClassification.DISABLED, "api_provider_disabled")
        if not self._api_key_value:
            self._fail(ApiPlannerClassification.KEY_MISSING, "api_provider_key_missing")
        if not self.model_id:
            self._fail(
                ApiPlannerClassification.MODEL_MISSING, "api_provider_model_missing"
            )
        valid = {tool.value for tool in ToolName}
        if not allowed_tools or any(tool not in valid for tool in allowed_tools):
            self._fail(
                ApiPlannerClassification.TOOLPLAN_UNSAFE, "allowed_tools_invalid"
            )
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
        billing = any(
            marker in body
            for marker in ("billing", "payment", "credit", "insufficient_quota")
        )
        quota = any(
            marker in body
            for marker in ("quota", "resource_exhausted", "limit exceeded")
        )
        if exc.status == 401:
            self._fail(ApiPlannerClassification.AUTH_INVALID, "PROVIDER_AUTH_ERROR")
        if exc.status == 403:
            self._fail(
                (
                    ApiPlannerClassification.BILLING_REQUIRED
                    if billing
                    else ApiPlannerClassification.AUTH_INVALID
                ),
                "PROVIDER_AUTH_ERROR",
            )
        if exc.status == 404:
            self._fail(
                ApiPlannerClassification.MODEL_UNAVAILABLE, "PROVIDER_UNAVAILABLE"
            )
        if exc.status == 429:
            self._fail(
                (
                    ApiPlannerClassification.QUOTA_EXCEEDED
                    if quota or billing
                    else ApiPlannerClassification.RATE_LIMITED
                ),
                "PROVIDER_RATE_LIMITED",
            )
        if exc.status >= 500:
            self._fail(
                ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_UNAVAILABLE"
            )
        self._fail(ApiPlannerClassification.NETWORK_ERROR, "PROVIDER_HTTP_ERROR")

    def _fail(self, classification: ApiPlannerClassification, safe_code: str) -> None:
        self._metadata.update(
            classification=classification.value, safe_error_code=safe_code
        )
        raise ApiPlannerError(classification, safe_code)


def _planner_instruction(
    *, smoke: bool = False, allowed_tools: Sequence[str] = ()
) -> str:
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
        'Canonical tool arguments: list_files {"path":"."}; glob_files {"pattern":"**/*","path":"."}; grep_search {"query":"text","path":"."}; read_file {"path":"src/main.cpp"}; write_file {"path":"src/main.cpp","content":"..."}. '
        "Prefer list/glob/grep/read before editing when context is missing. For create/build/generate tasks, inspect briefly and then make file changes; do not repeat read-only planning when the workspace state is already known. Prefer apply_patch for precise existing-file changes, use edit_file_simple for a single exact replacement, and use write_file only for new files or deliberate full replacement. "
        "For multi-file projects, write each actual file using a project-relative path such as include/SystemManager.h or src/SystemManager.cpp. write_file creates missing parent directories automatically. Never call write_file for a directory alone (such as include or src), and never use an absolute path or a path outside the active project. You may include up to five independent file writes in one tool plan. "
        "Use update_plan for multi-step work. Mark exactly one current step in_progress before acting, then complete it only after successful tool evidence. Each step may include verification: inspection, edit, build, or simulation; use build or simulation for those checks. Keep prior step text stable across updates. The summary is public progress, not private reasoning: one or two concise sentences describing what you are checking or have observed. Never output hidden thinking, scratchpads, or reasoning traces. Use build_firmware after meaningful firmware edits when build is available. "
        "Read build errors and repair them in later turns instead of stopping after the first failure. If a package is missing, use a registry package ID from dependency_candidates; do not guess publisher names. A tool edit with modified=false or an empty diff made no change. "
        "Use spawn_subagent only for a bounded Explore, Review, or Verify task that benefits from independent context; child agents cannot edit. "
        "Use load_skill and memory_search only when they are relevant. In plan-only contexts, inspect/search and call update_plan, never request mutation tools. Never invent tool results. "
        "Use workspace-relative paths only and never request hidden/credential paths. "
        "Set final=true with no tool calls only when the requested task is actually complete or no further safe action is possible. "
        "On a final turn, put the concise user-facing answer or completion summary in summary; do not use a generic word like completed. "
        "For read-only questions, inspect only what is needed and use the final summary to directly answer the user with concrete findings."
    )


def _parse_toolplan_content(content: object) -> tuple[ProductToolPlan, bool]:
    if isinstance(content, Mapping):
        decoded: object = dict(content)
        try:
            return _normalize_parsed_toolplan(
                ProductToolPlan.parse(decoded, max_calls=5)
            )
        except ValueError:
            normalized = _normalize_toolplan(decoded)
            plan, _ = _normalize_parsed_toolplan(
                ProductToolPlan.parse(normalized, max_calls=5)
            )
            return plan, True
    if not isinstance(content, str):
        raise ValueError("product_toolplan_json_invalid")
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip().casefold() in {"```", "```json"}
            and lines[-1].strip() == "```"
        ):
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        decoded = json.loads(candidate)
    except json.JSONDecodeError as exc:
        extracted = _extract_json_object(candidate)
        if extracted is None:
            raise ValueError("product_toolplan_json_invalid") from exc
        try:
            decoded = json.loads(extracted)
        except json.JSONDecodeError as nested:
            raise ValueError("product_toolplan_json_invalid") from nested
    try:
        return _normalize_parsed_toolplan(ProductToolPlan.parse(decoded, max_calls=5))
    except ValueError:
        normalized = _normalize_toolplan(decoded)
        plan, _ = _normalize_parsed_toolplan(
            ProductToolPlan.parse(normalized, max_calls=5)
        )
        return plan, True


def _extract_json_object(value: str) -> str | None:
    start = value.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(value[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return value[start : index + 1]
    return None


def _normalize_parsed_toolplan(plan: ProductToolPlan) -> tuple[ProductToolPlan, bool]:
    normalized_calls: list[ProductToolCall] = []
    changed = False
    for call in plan.tool_calls:
        try:
            parsed_tool = ToolName(call.tool_type)
        except ValueError:
            normalized_calls.append(call)
            continue
        normalized_tool, normalized_arguments = _normalize_tool_arguments(
            parsed_tool, dict(call.arguments)
        )
        if normalized_tool.value != call.tool_type or normalized_arguments != dict(
            call.arguments
        ):
            changed = True
        normalized_calls.append(
            ProductToolCall(call.call_id, normalized_tool.value, normalized_arguments)
        )
    if not changed:
        return plan, False
    return (
        ProductToolPlan(
            plan.version, plan.summary, tuple(normalized_calls), plan.final
        ),
        True,
    )


def _normalize_toolplan(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("product_toolplan_schema_invalid")
    allowed_top = {"version", "summary", "tool_calls", "files", "final"}
    if not set(value).issubset(allowed_top) or (
        "tool_calls" in value and "files" in value
    ):
        raise ValueError("product_toolplan_schema_invalid")
    raw_calls = value.get("tool_calls", value.get("files"))
    if not isinstance(raw_calls, list) or not raw_calls or len(raw_calls) > 5:
        raise ValueError("product_toolplan_schema_invalid")
    calls = [
        _normalize_tool_call(item, index)
        for index, item in enumerate(raw_calls, start=1)
    ]
    version = value.get("version", PRODUCT_TOOLPLAN_VERSION)
    summary = value.get("summary", "Generate requested project files")
    final = value.get("final", False)
    return {
        "version": version,
        "summary": summary,
        "tool_calls": calls,
        "final": final,
    }


def _toolplan_parse_error(exc: ValueError) -> ApiPlannerError:
    error = str(exc)
    reason = error if error.startswith(("product_", "provider_")) and len(error) <= 64 and error.replace("_", "").islower() and error.replace("_", "").isalpha() else "parse_error_unknown"
    details = {"parse_reason": reason}
    if error == "product_toolplan_json_invalid":
        return ApiPlannerError(
            ApiPlannerClassification.OUTPUT_NOT_JSON, "MODEL_OUTPUT_NOT_JSON", details
        )
    if error == "product_toolplan_version_invalid":
        return ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_INVALID, "TOOLPLAN_VERSION_INVALID", details
        )
    if error in {"provider_tool_unknown", "product_tool_not_available"}:
        return ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_INVALID, "TOOLPLAN_TOOL_UNKNOWN", details
        )
    if error == "product_tool_arguments_invalid" or "tool_call" in error:
        return ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_INVALID, "TOOL_ARGUMENTS_INVALID", details
        )
    return ApiPlannerError(
        ApiPlannerClassification.TOOLPLAN_INVALID, "TOOLPLAN_SCHEMA_INVALID", details
    )


def _normalize_tool_call(value: object, index: int) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("product_tool_call_schema_invalid")
    call_id = value.get("id") or f"call_{index}"
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
    if (
        not isinstance(tool_type, str)
        or tool_type == "function"
        or not isinstance(arguments, Mapping)
    ):
        raise ValueError("product_tool_call_schema_invalid")
    try:
        parsed_tool = parse_tool_name(tool_type)
    except ValueError as exc:
        raise ValueError("product_tool_call_schema_invalid") from exc
    parsed_tool, normalized_arguments = _normalize_tool_arguments(
        parsed_tool, dict(arguments)
    )
    return {"id": call_id, "type": parsed_tool.value, "arguments": normalized_arguments}


def _normalize_tool_arguments(
    tool: ToolName, arguments: dict[str, object]
) -> tuple[ToolName, dict[str, object]]:
    if tool is ToolName.LIST_FILES:
        path = arguments.get("path", ".")
        pattern = arguments.get("pattern")
        if isinstance(pattern, str) and pattern.strip():
            return ToolName.GLOB_FILES, {
                "path": _normalize_optional_directory_path(path),
                "pattern": _normalize_glob_pattern(pattern),
            }
        if path is None or path == "":
            arguments["path"] = "."
            return tool, arguments
        if isinstance(path, str) and _looks_like_glob(path):
            return ToolName.GLOB_FILES, {
                "path": ".",
                "pattern": _normalize_glob_pattern(path),
            }
        if isinstance(path, str):
            arguments["path"] = _normalize_optional_directory_path(path)
        return tool, arguments

    if tool is ToolName.GLOB_FILES:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            for alias in ("glob", "query"):
                alias_value = arguments.get(alias)
                if isinstance(alias_value, str) and alias_value.strip():
                    pattern = alias_value
                    break
            patterns = arguments.get("patterns")
            if (not isinstance(pattern, str) or not pattern.strip()) and isinstance(
                patterns, list
            ):
                for item in patterns:
                    if isinstance(item, str) and item.strip():
                        pattern = item
                        break
        path = arguments.get("path", ".")
        if (
            (not isinstance(pattern, str) or not pattern.strip())
            and isinstance(path, str)
            and _looks_like_glob(path)
        ):
            pattern = path
            path = "."
        if isinstance(pattern, str) and pattern.strip():
            return ToolName.GLOB_FILES, {
                "path": _normalize_optional_directory_path(path),
                "pattern": _normalize_glob_pattern(pattern),
            }
        return tool, arguments

    if tool is ToolName.GREP_SEARCH:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            pattern = arguments.get("pattern")
            if isinstance(pattern, str) and pattern.strip():
                arguments["query"] = pattern
                arguments.pop("pattern", None)
        path = arguments.get("path")
        if path is None or path == "":
            arguments["path"] = "."
        elif isinstance(path, str):
            arguments["path"] = _normalize_optional_directory_path(path)
        return tool, arguments

    if tool is ToolName.READ_FILE:
        path = arguments.get("path")
        if isinstance(path, str):
            arguments["path"] = _strip_current_directory_prefix(path.strip())
        return tool, arguments

    if tool is ToolName.BUILD_FIRMWARE:
        environment = arguments.get("environment")
        if isinstance(environment, str) and environment.strip():
            return tool, {"environment": environment.strip()}
        return tool, {}

    return tool, arguments


def _normalize_optional_directory_path(value: object) -> object:
    if value is None:
        return "."
    if not isinstance(value, str):
        return value
    path = value.strip()
    if path in {"", ".", "./"}:
        return "."
    return _strip_current_directory_prefix(path).rstrip("/") or "."


def _normalize_glob_pattern(value: str) -> str:
    pattern = _strip_current_directory_prefix(value.strip())
    return pattern or "**/*"


def _strip_current_directory_prefix(value: str) -> str:
    while value.startswith("./"):
        value = value[2:]
    return value


def _looks_like_glob(value: str) -> bool:
    return any(marker in value for marker in ("*", "?", "["))


def _is_smoke_task(task: str) -> bool:
    return "smoke" in task.casefold()


def _validate_generated_plan(
    plan: ProductToolPlan,
    *,
    smoke: bool,
    allowed_tools: Sequence[str],
    task: str = "",
    tool_results: Sequence[Mapping[str, object]] = (),
    turn: int = 1,
) -> None:
    if smoke:
        _validate_exact_smoke_plan(plan)
        return
    required_subagent = _required_subagent_role(task)
    subagent_completed = required_subagent is not None and any(
        result.get("tool") == ToolName.SPAWN_SUBAGENT.value
        and result.get("status") == "completed"
        for result in tool_results
    )
    if plan.final:
        if plan.tool_calls:
            raise ApiPlannerError(
                ApiPlannerClassification.TOOLPLAN_UNSAFE,
                "provider_toolplan_final_has_calls",
            )
        if required_subagent is not None and not subagent_completed:
            raise ApiPlannerError(
                ApiPlannerClassification.TOOLPLAN_INVALID,
                "provider_required_subagent_missing",
            )
        return
    if not plan.tool_calls:
        raise ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_toolplan_empty"
        )
    allowed = set(allowed_tools)
    if required_subagent is not None and not subagent_completed:
        delegated = [
            call
            for call in plan.tool_calls
            if call.tool_type == ToolName.SPAWN_SUBAGENT.value
            and call.arguments.get("role") == required_subagent
        ]
        if len(delegated) != 1:
            raise ApiPlannerError(
                ApiPlannerClassification.TOOLPLAN_INVALID,
                "provider_required_subagent_missing",
            )
    write_tool_names = {tool.value for tool in WRITE_TOOLS}
    for call in plan.tool_calls:
        if call.tool_type not in allowed:
            raise ApiPlannerError(
                ApiPlannerClassification.TOOLPLAN_UNSAFE,
                "provider_requested_forbidden_tool",
                _toolplan_rejection_details(call),
            )
        path = call.arguments.get("path")
        if (
            call.tool_type in write_tool_names
            and isinstance(path, str)
            and not is_safe_relative_path(path)
        ):
            raise ApiPlannerError(
                ApiPlannerClassification.TOOLPLAN_UNSAFE,
                "provider_path_unsafe",
                _toolplan_rejection_details(call),
            )


def _required_subagent_role(task: str) -> str | None:
    match = re.search(
        r"must delegate exactly one bounded ['\"](explore|review|verify)['\"] subagent",
        task,
        flags=re.IGNORECASE,
    )
    return match.group(1).casefold() if match else None


def _toolplan_rejection_details(call: ProductToolCall) -> dict[str, object]:
    details: dict[str, object] = {
        "tool": call.tool_type,
        "tool_call_id": call.call_id,
    }
    path = call.arguments.get("path")
    if isinstance(path, str) and path and len(path) <= 300 and path.isascii():
        details["path"] = path
    query = call.arguments.get("query")
    if isinstance(query, str) and query and len(query) <= 120 and query.isascii():
        details["query"] = query
    return details


def _validate_exact_smoke_plan(plan: ProductToolPlan) -> None:
    if plan.final or len(plan.tool_calls) != 1:
        raise ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_UNSAFE,
            "provider_smoke_call_count_invalid",
        )
    call = plan.tool_calls[0]
    if (
        call.tool_type != "write_file"
        or call.path != API_PRODUCT_SMOKE_PATH
        or call.content != API_PRODUCT_SMOKE_CONTENT
    ):
        raise ApiPlannerError(
            ApiPlannerClassification.TOOLPLAN_UNSAFE, "provider_smoke_toolplan_unsafe"
        )


def _provider_response_summary(response: ModelResponse) -> dict[str, object]:
    summary: dict[str, object] = {}
    if response.request_id:
        summary["provider_request_id"] = response.request_id[:128]
    if response.input_tokens is not None:
        summary["input_tokens"] = response.input_tokens
    if response.output_tokens is not None:
        summary["output_tokens"] = response.output_tokens
    if response.finish_reason:
        summary["finish_reason"] = response.finish_reason
    return summary


def _safe_provider_error_message(body: str, status: int) -> str:
    if body:
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError:
            return body[:1000]
        if isinstance(decoded, Mapping):
            error = decoded.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                code = error.get("code")
                if isinstance(message, str) and message.strip():
                    suffix = f" code={code}" if isinstance(code, (str, int)) else ""
                    return (message.strip() + suffix)[:1000]
            message = decoded.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()[:1000]
    return f"provider_http_status_{status}"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def post_chat_completion(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    extra_headers: Mapping[str, str],
    timeout_seconds: float,
) -> Mapping[str, object]:
    if urlparse(url).scheme != "https":
        raise ApiPlannerError(
            ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe"
        )
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
        raise ApiPlannerError(
            ApiPlannerClassification.RESPONSE_INVALID, "provider_response_too_large"
        )
    try:
        decoded: Any = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiPlannerError(
            ApiPlannerClassification.RESPONSE_INVALID,
            "provider_response_envelope_invalid",
        ) from exc
    if not isinstance(decoded, Mapping):
        raise ApiPlannerError(
            ApiPlannerClassification.RESPONSE_INVALID,
            "provider_response_envelope_invalid",
        )
    return decoded


def stream_chat_completion(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    extra_headers: Mapping[str, str],
    timeout_seconds: float,
    on_content_delta: Callable[[str, str], None] | None = None,
) -> Mapping[str, object]:
    """Consume an OpenAI-compatible ``data:`` SSE stream into one safe envelope."""
    if urlparse(url).scheme != "https":
        raise ApiPlannerError(
            ApiPlannerClassification.UNSAFE_ABORTED, "provider_url_unsafe"
        )
    request_payload = dict(payload)
    request_payload["stream"] = True
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        **extra_headers,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(request_payload, ensure_ascii=True).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    content_parts: list[str] = []
    content_bytes = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            for raw_line in response:
                if not raw_line:
                    continue
                if len(raw_line) > MAX_API_RESPONSE_BYTES + 4096:
                    raise ApiPlannerError(
                        ApiPlannerClassification.RESPONSE_INVALID,
                        "provider_response_too_large",
                    )
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError as exc:
                    raise ApiPlannerError(
                        ApiPlannerClassification.RESPONSE_INVALID,
                        "provider_response_envelope_invalid",
                    ) from exc
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ApiPlannerError(
                        ApiPlannerClassification.RESPONSE_INVALID,
                        "provider_stream_event_invalid",
                    ) from exc
                try:
                    canonical_events = normalize_stream_event(
                        event, provider_id=_provider_id_from_url(url)
                    )
                except ProviderNormalizationError as exc:
                    raise ApiPlannerError(
                        ApiPlannerClassification.RESPONSE_INVALID,
                        exc.code,
                    ) from exc
                for canonical in canonical_events:
                    if canonical.type == "error":
                        raise ApiPlannerError(
                            ApiPlannerClassification.RESPONSE_INVALID,
                            canonical.error_code or "PROVIDER_STREAM_INVALID",
                        )
                    # Reasoning is intentionally separate and never becomes
                    # final ToolPlan JSON or public summary text.
                    if canonical.type != "text_delta" or not canonical.text:
                        continue
                    piece = canonical.text
                    content_bytes += len(piece.encode("utf-8"))
                    if content_bytes > MAX_API_RESPONSE_BYTES:
                        raise ApiPlannerError(
                            ApiPlannerClassification.RESPONSE_INVALID,
                            "PROVIDER_RESPONSE_TOO_LARGE",
                        )
                    content_parts.append(piece)
                    if on_content_delta is not None:
                        on_content_delta(piece, "".join(content_parts))
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        request_id = exc.headers.get("x-request-id") or exc.headers.get("cf-ray")
        raise ApiTransportHTTPError(exc.code, body, request_id) from exc
    content = "".join(content_parts)
    if not content:
        raise ApiPlannerError(
            ApiPlannerClassification.RESPONSE_INVALID,
            "provider_response_content_invalid",
        )
    return {"output_text": content}


def _provider_id_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").casefold()
    if "openrouter" in host:
        return "openrouter"
    if "groq" in host:
        return "groq"
    if "googleapis" in host:
        return "gemini"
    return "openai"


def _partial_json_string_value(text: str, key: str) -> str:
    """Decode the currently available prefix of a JSON string value.

    It is intentionally narrow: only a top-level-ish quoted field is extracted.
    Invalid/incomplete escapes simply stop extraction until the next stream chunk.
    """
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*"', text)
    if match is None:
        return ""
    index = match.end()
    out: list[str] = []
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(text):
        char = text[index]
        if char == '"':
            break
        if char != "\\":
            out.append(char)
            index += 1
            continue
        if index + 1 >= len(text):
            break
        code = text[index + 1]
        if code == "u":
            if index + 6 > len(text):
                break
            digits = text[index + 2 : index + 6]
            try:
                out.append(chr(int(digits, 16)))
            except ValueError:
                break
            index += 6
            continue
        mapped = escapes.get(code)
        if mapped is None:
            break
        out.append(mapped)
        index += 2
    return "".join(out)


def _safe_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if not all(character.isalnum() or character in "._:-" for character in candidate):
        return None
    return candidate
