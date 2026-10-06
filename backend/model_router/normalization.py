"""Provider-envelope adapters for ForgeX's canonical model contracts.

Only this module knows provider response field names. Router, Chat, Agent and
ToolPlan validation consume canonical values and never inspect raw envelopes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import CanonicalStreamEvent, ModelResponse


class ProviderNormalizationError(ValueError):
    """A successful HTTP response could not be normalized safely."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NormalizedEnvelope:
    text: str | None
    structured_output: dict[str, Any] | None
    tool_calls: tuple[dict[str, Any], ...]
    reasoning: str | None
    finish_reason: str | None
    usage: dict[str, int]
    request_id: str | None
    model_id: str | None
    truncated: bool

    def to_response(
        self, *, provider_id: str, model_id: str, latency_ms: int = 0
    ) -> ModelResponse:
        return ModelResponse(
            content=self.text or "",
            provider_id=provider_id,
            model_id=self.model_id or model_id,
            latency_ms=latency_ms,
            input_tokens=self.usage.get("input_tokens"),
            output_tokens=self.usage.get("output_tokens"),
            total_tokens=self.usage.get("total_tokens"),
            raw_usage=dict(self.usage),
            structured_output=self.structured_output,
            tool_calls=self.tool_calls,
            finish_reason=self.finish_reason,
            request_id=self.request_id,
            reasoning=self.reasoning,
            truncated=self.truncated,
        )


class CanonicalStreamAssembler:
    """Assemble canonical deltas without knowing provider wire formats."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._tools: dict[int, dict[str, Any]] = {}
        self._finish_reason: str | None = None
        self._usage: dict[str, int] = {}
        self._done = False

    def add(self, event: CanonicalStreamEvent) -> None:
        if event.type == "error":
            raise ProviderNormalizationError(
                event.error_code or "PROVIDER_STREAM_INVALID",
                "Provider stream reported an error.",
            )
        if event.type == "text_delta" and event.text:
            self._text.append(event.text)
            return
        if event.type == "reasoning_delta" and event.text:
            self._reasoning.append(event.text)
            return
        if event.type == "tool_call_delta":
            self._add_tool(event)
            return
        if event.type == "done":
            self._done = True
            self._finish_reason = event.finish_reason
            self._usage.update(event.usage)

    def _add_tool(self, event: CanonicalStreamEvent) -> None:
        raw = event.tool_call or {}
        index = event.tool_call_index
        if index is None:
            raw_index = raw.get("index")
            index = raw_index if isinstance(raw_index, int) else len(self._tools)
        state = self._tools.setdefault(
            index, {"id": None, "name": "", "argument_parts": [], "arguments": None}
        )
        call_id = event.tool_call_id or _optional_text(raw.get("id"))
        if call_id:
            state["id"] = call_id
        name = event.tool_name or _optional_text(raw.get("name"))
        if name:
            current = str(state["name"])
            state["name"] = name if not current or name.startswith(current) else current + name
        arguments = event.arguments
        if arguments is None and isinstance(raw.get("arguments"), Mapping):
            arguments = dict(raw["arguments"])
        if arguments is not None:
            state["arguments"] = dict(arguments)
        fragment = event.arguments_delta
        if fragment is None and isinstance(raw.get("arguments"), str):
            fragment = str(raw["arguments"])
        if fragment:
            state["argument_parts"].append(fragment)

    def response(self, *, provider_id: str, model_id: str) -> ModelResponse:
        if not self._done:
            raise ProviderNormalizationError(
                "PROVIDER_STREAM_INVALID", "Provider stream ended before completion."
            )
        calls: list[dict[str, Any]] = []
        for index in sorted(self._tools):
            state = self._tools[index]
            name = str(state["name"])
            arguments = state["arguments"]
            if arguments is None:
                document = "".join(state["argument_parts"])
                try:
                    arguments = json.loads(document)
                except json.JSONDecodeError as exc:
                    raise ProviderNormalizationError(
                        "TOOL_ARGUMENTS_INVALID",
                        "Streamed tool arguments are not valid JSON.",
                    ) from exc
            if not name or not isinstance(arguments, Mapping):
                raise ProviderNormalizationError(
                    "TOOL_ARGUMENTS_INVALID", "Streamed tool call is incomplete."
                )
            calls.append({"id": state["id"], "name": name, "arguments": dict(arguments)})
        text = "".join(self._text)
        truncated = self._finish_reason in {"length", "max_tokens", "incomplete", "MAX_TOKENS"}
        if not text and not calls:
            raise ProviderNormalizationError(
                "MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_OUTPUT_EMPTY",
                "Model stream returned no final content or tool call.",
            )
        structured = _json_object_or_none(text) if text else None
        return ModelResponse(
            content=text,
            provider_id=provider_id,
            model_id=model_id,
            input_tokens=self._usage.get("input_tokens"),
            output_tokens=self._usage.get("output_tokens"),
            total_tokens=self._usage.get("total_tokens"),
            raw_usage=dict(self._usage),
            structured_output=structured,
            tool_calls=tuple(calls),
            finish_reason=self._finish_reason,
            reasoning="".join(self._reasoning) or None,
            truncated=truncated,
        )


def normalize_response(
    data: Mapping[str, Any], *, provider_id: str, model_id: str
) -> NormalizedEnvelope:
    """Normalize one complete response using the provider's wire adapter."""

    if not isinstance(data, Mapping):
        raise ProviderNormalizationError(
            "PROVIDER_ENVELOPE_INVALID", "Provider response must be an object."
        )
    embedded = data.get("error")
    if embedded:
        raise ProviderNormalizationError(
            "PROVIDER_ENVELOPE_INVALID", "Provider returned an embedded error."
        )
    if provider_id == "gemini" and "candidates" in data:
        return _normalize_gemini(data, model_id=model_id)
    if provider_id == "anthropic" and "content" in data:
        return _normalize_anthropic(data, model_id=model_id)
    if provider_id == "ollama" and ("message" in data or data.get("done") is True):
        return _normalize_ollama(data, model_id=model_id)
    return _normalize_openai_compatible(data, model_id=model_id)


def normalize_stream_event(
    data: Mapping[str, Any], *, provider_id: str
) -> tuple[CanonicalStreamEvent, ...]:
    """Convert a provider stream packet into typed canonical events."""

    if not isinstance(data, Mapping):
        raise ProviderNormalizationError(
            "PROVIDER_STREAM_INVALID", "Provider stream packet must be an object."
        )
    if data.get("error"):
        return (CanonicalStreamEvent("error", error_code="PROVIDER_STREAM_INVALID"),)
    # The HTTP/SSE reader converts the transport-level ``[DONE]`` sentinel to
    # this provider-neutral packet. Handle it before choosing a wire adapter so
    # every provider can terminate a stream consistently.
    if data.get("type") == "forgex.stream.done":
        return (CanonicalStreamEvent("done", finish_reason="stop"),)
    if provider_id == "gemini" and "candidates" in data:
        events: list[CanonicalStreamEvent] = []
        candidates = data.get("candidates")
        candidate = (
            candidates[0]
            if isinstance(candidates, Sequence)
            and not isinstance(candidates, (str, bytes))
            and candidates
            and isinstance(candidates[0], Mapping)
            else None
        )
        if candidate is None:
            # Metadata-only Gemini stream packets are valid and carry no delta.
            if data.get("usageMetadata") is not None:
                return ()
            raise ProviderNormalizationError(
                "PROVIDER_STREAM_INVALID", "Gemini stream candidate is invalid."
            )
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, Mapping) else ()
        if isinstance(parts, Sequence) and not isinstance(parts, (str, bytes)):
            for part in parts:
                if not isinstance(part, Mapping):
                    continue
                part_text = _text_value(part.get("text"))
                if part_text:
                    events.append(
                        CanonicalStreamEvent(
                            "reasoning_delta" if part.get("thought") is True else "text_delta",
                            text=part_text,
                        )
                    )
                function = part.get("functionCall")
                if isinstance(function, Mapping):
                    name = _optional_text(function.get("name"))
                    arguments = function.get("args")
                    if name and isinstance(arguments, Mapping):
                        events.append(
                            CanonicalStreamEvent(
                                "tool_call_delta",
                                tool_call={
                                    "id": _optional_text(function.get("id")),
                                    "name": name,
                                    "arguments": dict(arguments),
                                },
                            )
                        )
        finish_reason = _optional_text(candidate.get("finishReason"))
        if finish_reason:
            events.append(
                CanonicalStreamEvent(
                    "done",
                    finish_reason=finish_reason,
                    usage=_normalize_usage(data.get("usageMetadata")),
                )
            )
        return tuple(events)
    if provider_id == "anthropic":
        event_type = data.get("type")
        delta = data.get("delta")
        index = data.get("index")
        content_block = data.get("content_block")
        if event_type == "content_block_start" and isinstance(content_block, Mapping):
            if content_block.get("type") == "tool_use":
                initial = content_block.get("input")
                return (CanonicalStreamEvent(
                    "tool_call_delta",
                    tool_call_index=index if isinstance(index, int) else 0,
                    tool_call_id=_optional_text(content_block.get("id")),
                    tool_name=_optional_text(content_block.get("name")),
                    arguments=dict(initial) if isinstance(initial, Mapping) and initial else None,
                ),)
        if event_type == "content_block_delta" and isinstance(delta, Mapping):
            delta_type = delta.get("type")
            text = _text_value(delta.get("text", delta.get("thinking")))
            if text and delta_type in {"text_delta", "thinking_delta"}:
                return (
                    CanonicalStreamEvent(
                        "reasoning_delta" if delta_type == "thinking_delta" else "text_delta",
                        text=text,
                    ),
                )
            partial_json = delta.get("partial_json")
            if delta_type == "input_json_delta" and isinstance(partial_json, str):
                return (CanonicalStreamEvent(
                    "tool_call_delta",
                    tool_call_index=index if isinstance(index, int) else 0,
                    arguments_delta=partial_json,
                ),)
        if event_type == "message_stop":
            return (CanonicalStreamEvent("done", finish_reason="stop"),)
        if isinstance(event_type, str):
            return ()
        raise ProviderNormalizationError(
            "PROVIDER_STREAM_INVALID", "Anthropic stream packet is invalid."
        )
    if provider_id == "ollama" and ("message" in data or data.get("done") is True):
        message = data.get("message")
        events = []
        if isinstance(message, Mapping):
            reasoning = _text_value(message.get("thinking"))
            text = _text_value(message.get("content"))
            if reasoning:
                events.append(CanonicalStreamEvent("reasoning_delta", text=reasoning))
            if text:
                events.append(CanonicalStreamEvent("text_delta", text=text))
            events.extend(
                CanonicalStreamEvent("tool_call_delta", tool_call=call)
                for call in _normalize_tool_calls(message.get("tool_calls"))
            )
        if data.get("done") is True:
            events.append(CanonicalStreamEvent("done", finish_reason=_optional_text(data.get("done_reason")) or "stop", usage=_normalize_usage(data)))
        return tuple(events)
    return _normalize_openai_stream(data)


def response_payload(response: ModelResponse) -> object:
    """Return the structured value an Agent planner should validate."""

    if response.structured_output is not None:
        return response.structured_output
    for call in response.tool_calls:
        if call.get("name") in {"submit_tool_plan", "forgex_tool_plan"}:
            return call.get("arguments")
    return response.text


def _normalize_openai_compatible(
    data: Mapping[str, Any], *, model_id: str
) -> NormalizedEnvelope:
    choice: Mapping[str, Any] | None = None
    choices = data.get("choices")
    if isinstance(choices, Sequence) and not isinstance(choices, (str, bytes)) and choices:
        choice = choices[0] if isinstance(choices[0], Mapping) else None
        if choice is None:
            raise ProviderNormalizationError(
                "PROVIDER_ENVELOPE_INVALID", "Provider choice must be an object."
            )
    message = choice.get("message") if choice else None
    message = message if isinstance(message, Mapping) else {}
    text = _text_value(message.get("content"))
    if text is None and choice:
        text = _text_value(choice.get("text"))
    if text is None:
        text = _text_value(data.get("output_text"))
    if text is None:
        text = _responses_output_text(data.get("output"))
    reasoning = _reasoning_value(message) or _reasoning_value(choice or {})
    tool_calls = _normalize_tool_calls(message.get("tool_calls"))
    if not tool_calls:
        tool_calls = _responses_tool_calls(data.get("output"))
    structured = _structured_value(message.get("parsed"))
    if structured is None:
        structured = _structured_value(data.get("structured_output"))
    if structured is None and text:
        structured = _json_object_or_none(text)
    finish = choice.get("finish_reason") if choice else data.get("status")
    finish_reason = finish if isinstance(finish, str) else None
    truncated = finish_reason in {"length", "max_tokens", "incomplete"} or data.get("status") == "incomplete"
    if not text and not tool_calls and structured is None:
        code = "MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_OUTPUT_EMPTY"
        raise ProviderNormalizationError(code, "Model returned no final content or tool call.")
    usage = _normalize_usage(data.get("usage"))
    return NormalizedEnvelope(
        text=text or None,
        structured_output=structured,
        tool_calls=tool_calls,
        reasoning=reasoning,
        finish_reason=finish_reason,
        usage=usage,
        request_id=_optional_text(data.get("id")),
        model_id=_optional_text(data.get("model")) or model_id,
        truncated=truncated,
    )


def _normalize_gemini(data: Mapping[str, Any], *, model_id: str) -> NormalizedEnvelope:
    candidates = data.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
        raise ProviderNormalizationError("MODEL_OUTPUT_EMPTY", "Gemini returned no candidate.")
    candidate = candidates[0]
    if not isinstance(candidate, Mapping):
        raise ProviderNormalizationError("PROVIDER_ENVELOPE_INVALID", "Gemini candidate must be an object.")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)):
        parts = ()
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        text = _text_value(part.get("text"))
        if text:
            (reasoning_parts if part.get("thought") is True else text_parts).append(text)
        function = part.get("functionCall")
        if isinstance(function, Mapping):
            name = _optional_text(function.get("name"))
            args = function.get("args")
            if name and isinstance(args, Mapping):
                calls.append({"id": _optional_text(function.get("id")), "name": name, "arguments": dict(args)})
    finish = _optional_text(candidate.get("finishReason"))
    truncated = finish in {"MAX_TOKENS", "RECITATION"}
    if not text_parts and not calls:
        code = "MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_OUTPUT_EMPTY"
        raise ProviderNormalizationError(code, "Gemini returned no final content or function call.")
    return NormalizedEnvelope(
        text="".join(text_parts) or None,
        structured_output=_json_object_or_none("".join(text_parts)),
        tool_calls=tuple(calls),
        reasoning="".join(reasoning_parts) or None,
        finish_reason=finish,
        usage=_normalize_usage(data.get("usageMetadata")),
        request_id=_optional_text(data.get("responseId")),
        model_id=_optional_text(data.get("modelVersion")) or model_id,
        truncated=truncated,
    )


def _normalize_ollama(data: Mapping[str, Any], *, model_id: str) -> NormalizedEnvelope:
    message = data.get("message")
    if not isinstance(message, Mapping):
        raise ProviderNormalizationError("PROVIDER_ENVELOPE_INVALID", "Ollama message is missing.")
    text = _text_value(message.get("content"))
    calls = _normalize_tool_calls(message.get("tool_calls"))
    if not text and not calls:
        raise ProviderNormalizationError("MODEL_OUTPUT_EMPTY", "Ollama returned no final content or tool call.")
    usage = _normalize_usage(data)
    return NormalizedEnvelope(
        text=text or None,
        structured_output=_json_object_or_none(text or ""),
        tool_calls=calls,
        reasoning=_text_value(message.get("thinking")) or None,
        finish_reason=(_optional_text(data.get("done_reason")) or "stop") if data.get("done") is True else None,
        usage=usage,
        request_id=None,
        model_id=_optional_text(data.get("model")) or model_id,
        truncated=data.get("done_reason") in {"length", "max_tokens"},
    )


def _normalize_anthropic(data: Mapping[str, Any], *, model_id: str) -> NormalizedEnvelope:
    blocks = data.get("content")
    if not isinstance(blocks, Sequence) or isinstance(blocks, (str, bytes)):
        raise ProviderNormalizationError(
            "PROVIDER_ENVELOPE_INVALID", "Anthropic content must be an array."
        )
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        text = _text_value(block.get("text", block.get("thinking")))
        if text and block_type == "text":
            text_parts.append(text)
        elif text and block_type in {"thinking", "reasoning"}:
            reasoning_parts.append(text)
        elif block_type == "tool_use":
            name = _optional_text(block.get("name"))
            arguments = block.get("input")
            if name and isinstance(arguments, Mapping):
                calls.append(
                    {
                        "id": _optional_text(block.get("id")),
                        "name": name,
                        "arguments": dict(arguments),
                    }
                )
    finish = _optional_text(data.get("stop_reason"))
    truncated = finish in {"max_tokens", "model_context_window_exceeded"}
    text_value = "".join(text_parts)
    if not text_value and not calls:
        raise ProviderNormalizationError(
            "MODEL_OUTPUT_TRUNCATED" if truncated else "MODEL_OUTPUT_EMPTY",
            "Anthropic returned no final content or tool call.",
        )
    return NormalizedEnvelope(
        text=text_value or None,
        structured_output=_json_object_or_none(text_value),
        tool_calls=tuple(calls),
        reasoning="".join(reasoning_parts) or None,
        finish_reason=finish,
        usage=_normalize_usage(data.get("usage")),
        request_id=_optional_text(data.get("id")),
        model_id=_optional_text(data.get("model")) or model_id,
        truncated=truncated,
    )


def _normalize_openai_stream(data: Mapping[str, Any]) -> tuple[CanonicalStreamEvent, ...]:
    choices = data.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)) or not choices:
        if data.get("usage") is not None:
            return (CanonicalStreamEvent("done", usage=_normalize_usage(data.get("usage"))),)
        # OpenAI Responses API typed events.
        event_type = data.get("type")
        delta = data.get("delta")
        if event_type in {"response.output_text.delta", "content.delta"} and isinstance(delta, str):
            return (CanonicalStreamEvent("text_delta", text=delta),)
        if event_type in {"response.reasoning_text.delta", "reasoning.delta"} and isinstance(delta, str):
            return (CanonicalStreamEvent("reasoning_delta", text=delta),)
        if event_type in {
            "response.function_call_arguments.delta",
            "response.tool_call_arguments.delta",
        } and isinstance(delta, str):
            index = data.get("output_index")
            return (CanonicalStreamEvent(
                "tool_call_delta",
                tool_call_index=index if isinstance(index, int) else 0,
                tool_call_id=_optional_text(data.get("item_id", data.get("call_id"))),
                tool_name=_optional_text(data.get("name")),
                arguments_delta=delta,
            ),)
        if event_type == "response.output_item.added":
            item = data.get("item")
            if isinstance(item, Mapping) and item.get("type") in {
                "function_call", "tool_call"
            }:
                raw_arguments = item.get("arguments")
                index = data.get("output_index")
                return (CanonicalStreamEvent(
                    "tool_call_delta",
                    tool_call_index=index if isinstance(index, int) else 0,
                    tool_call_id=_optional_text(item.get("call_id", item.get("id"))),
                    tool_name=_optional_text(item.get("name")),
                    arguments_delta=raw_arguments if isinstance(raw_arguments, str) else None,
                    arguments=dict(raw_arguments) if isinstance(raw_arguments, Mapping) else None,
                ),)
        if event_type in {"response.completed", "response.done"}:
            response = data.get("response")
            usage = response.get("usage") if isinstance(response, Mapping) else data.get("usage")
            return (CanonicalStreamEvent("done", finish_reason="stop", usage=_normalize_usage(usage)),)
        if "choices" in data or data.get("type") is not None:
            return ()
        raise ProviderNormalizationError(
            "PROVIDER_STREAM_INVALID", "Provider stream packet has no recognized event shape."
        )
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ProviderNormalizationError("PROVIDER_STREAM_INVALID", "Provider stream choice must be an object.")
    delta = choice.get("delta")
    if not isinstance(delta, Mapping):
        delta = {}
    events: list[CanonicalStreamEvent] = []
    reasoning = _reasoning_value(delta)
    text = _text_value(delta.get("content"))
    if reasoning:
        events.append(CanonicalStreamEvent("reasoning_delta", text=reasoning))
    if text:
        events.append(CanonicalStreamEvent("text_delta", text=text))
    for call in _normalize_tool_calls(delta.get("tool_calls"), partial=True):
        raw_arguments = call.get("arguments")
        events.append(CanonicalStreamEvent(
            "tool_call_delta",
            tool_call=call,
            tool_call_index=call.get("index") if isinstance(call.get("index"), int) else None,
            tool_call_id=_optional_text(call.get("id")),
            tool_name=_optional_text(call.get("name")),
            arguments_delta=raw_arguments if isinstance(raw_arguments, str) else None,
            arguments=dict(raw_arguments) if isinstance(raw_arguments, Mapping) else None,
        ))
    legacy_function = delta.get("function_call")
    if isinstance(legacy_function, Mapping):
        raw_arguments = legacy_function.get("arguments")
        events.append(CanonicalStreamEvent(
            "tool_call_delta",
            tool_call_index=0,
            tool_name=_optional_text(legacy_function.get("name")),
            arguments_delta=raw_arguments if isinstance(raw_arguments, str) else None,
        ))
    finish = _optional_text(choice.get("finish_reason"))
    if finish:
        events.append(CanonicalStreamEvent("done", finish_reason=finish, usage=_normalize_usage(data.get("usage"))))
    return tuple(events)


def _text_value(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        nested = value.get("value", value.get("text"))
        return nested if isinstance(nested, str) else None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and item.get("type") not in {"reasoning", "thinking"}:
                text = _text_value(item.get("text", item.get("content")))
                if text is not None:
                    parts.append(text)
        return "".join(parts)
    return None


def _reasoning_value(value: Mapping[str, Any]) -> str | None:
    for key in ("reasoning_content", "reasoning", "thinking"):
        text = _text_value(value.get(key))
        if text:
            return text
    content = value.get("content")
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts = [
            _text_value(item.get("text", item.get("content")))
            for item in content
            if isinstance(item, Mapping) and item.get("type") in {"reasoning", "thinking"}
        ]
        return "".join(part for part in parts if part) or None
    return None


def _normalize_tool_calls(value: object, *, partial: bool = False) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    calls: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        function = item.get("function")
        source = function if isinstance(function, Mapping) else item
        name = _optional_text(source.get("name", source.get("type")))
        raw_args = source.get("arguments", source.get("args"))
        arguments: object = raw_args
        if isinstance(raw_args, str) and not partial:
            try:
                arguments = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise ProviderNormalizationError("TOOL_ARGUMENTS_INVALID", "Provider tool arguments are not valid JSON.") from exc
        has_partial_identity = (
            partial
            and (
                name is not None
                or isinstance(item.get("index"), int)
                or _optional_text(item.get("id")) is not None
                or isinstance(raw_args, (str, Mapping))
            )
        )
        if (name and isinstance(arguments, Mapping)) or has_partial_identity:
            calls.append(
                {
                    "id": _optional_text(item.get("id")),
                    "name": name,
                    "arguments": arguments if partial else dict(arguments),
                    **({"index": item.get("index")} if isinstance(item.get("index"), int) else {}),
                }
            )
    return tuple(calls)


def _responses_output_text(value: object) -> str | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or item.get("type") not in {"message", None}:
            continue
        text = _text_value(item.get("content"))
        if text:
            parts.append(text)
    return "".join(parts) or None


def _responses_tool_calls(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    calls = [item for item in value if isinstance(item, Mapping) and item.get("type") in {"function_call", "tool_call"}]
    return _normalize_tool_calls(calls)


def _structured_value(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _json_object_or_none(value: str) -> dict[str, Any] | None:
    if not value.strip().startswith("{"):
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return dict(decoded) if isinstance(decoded, Mapping) else None


def _normalize_usage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens", "promptTokenCount", "prompt_eval_count"),
        "output_tokens": ("output_tokens", "completion_tokens", "candidatesTokenCount", "eval_count"),
        "total_tokens": ("total_tokens", "totalTokenCount"),
    }
    result: dict[str, int] = {}
    for target, keys in aliases.items():
        found = next((value.get(key) for key in keys if value.get(key) is not None), None)
        if isinstance(found, int) and not isinstance(found, bool) and found >= 0:
            result[target] = found
    if "total_tokens" not in result and {"input_tokens", "output_tokens"}.issubset(result):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
