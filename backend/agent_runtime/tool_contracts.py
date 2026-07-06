"""Strict, provider-neutral contracts for ForgeX-owned tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ToolName(str, Enum):
    LIST_FILES = "list_files"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    EDIT_FILE_SIMPLE = "edit_file_simple"
    CREATE_REVIEW = "create_review"


class RuntimeClassification(str, Enum):
    PASS = "TOOL_RUNTIME_PASS"
    PROVIDER_INVALID = "TOOL_RUNTIME_PROVIDER_INVALID"
    POLICY_DENIED = "TOOL_RUNTIME_POLICY_DENIED"
    PATH_UNSAFE = "TOOL_RUNTIME_PATH_UNSAFE"
    CONTENT_INVALID = "TOOL_RUNTIME_CONTENT_INVALID"
    EXTRA_CHANGES = "TOOL_RUNTIME_EXTRA_CHANGES"
    NO_CHANGES = "TOOL_RUNTIME_NO_CHANGES"
    REVIEW_CREATED = "TOOL_RUNTIME_REVIEW_CREATED"
    UNSAFE_ABORTED = "TOOL_RUNTIME_UNSAFE_ABORTED"
    UNKNOWN_SAFE_FAILURE = "TOOL_RUNTIME_UNKNOWN_SAFE_FAILURE"
    CANCELLED = "AGENT_RUNTIME_CANCELLED"
    MAX_TURNS = "AGENT_RUNTIME_MAX_TURNS"
    LIMIT_EXCEEDED = "AGENT_RUNTIME_LIMIT_EXCEEDED"


class RuntimeEventType(str, Enum):
    RUNTIME_STARTED = "runtime.started"
    PROVIDER_PLAN_REQUESTED = "provider.plan_requested"
    PROVIDER_PLAN_RECEIVED = "provider.plan_received"
    TOOL_VALIDATION_STARTED = "tool.validation.started"
    TOOL_VALIDATION_DENIED = "tool.validation.denied"
    TOOL_EXECUTION_STARTED = "tool.execution.started"
    TOOL_EXECUTION_COMPLETED = "tool.execution.completed"
    SANDBOX_SNAPSHOT_CREATED = "sandbox.snapshot.created"
    SANDBOX_DIFF_CREATED = "sandbox.diff.created"
    REVIEW_CANDIDATE_CREATED = "review.candidate.created"
    RUNTIME_COMPLETED = "runtime.completed"
    RUNTIME_FAILED = "runtime.failed"
    API_PROVIDER_REQUEST_STARTED = "api_provider.request.started"
    API_PROVIDER_REQUEST_COMPLETED = "api_provider.request.completed"
    API_PROVIDER_REQUEST_FAILED = "api_provider.request.failed"
    API_PROVIDER_PLAN_PARSED = "api_provider.plan.parsed"
    API_PROVIDER_PLAN_INVALID = "api_provider.plan.invalid"
    TOOL_RUNTIME_VALIDATION_STARTED = "tool_runtime.validation.started"
    TOOL_RUNTIME_VALIDATION_COMPLETED = "tool_runtime.validation.completed"
    TOOL_RUNTIME_EXECUTION_COMPLETED = "tool_runtime.execution.completed"
    API_PROVIDER_SMOKE_COMPLETED = "api_provider.smoke.completed"


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_type: RuntimeEventType
    sequence: int
    classification: str | None = None
    count: int | None = None

    def to_safe_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"event_type": self.event_type.value, "sequence": self.sequence}
        if self.classification is not None:
            payload["classification"] = self.classification
        if self.count is not None:
            payload["count"] = self.count
        return payload


@dataclass(frozen=True, slots=True)
class ToolCall:
    tool: ToolName
    arguments: Mapping[str, object] = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, value: object) -> "ToolCall":
        if not isinstance(value, Mapping) or set(value) != {"tool", "arguments"}:
            raise ValueError("provider_tool_call_invalid")
        raw_tool = value.get("tool")
        arguments = value.get("arguments")
        if not isinstance(raw_tool, str) or not isinstance(arguments, Mapping):
            raise ValueError("provider_tool_call_invalid")
        try:
            tool = ToolName(raw_tool)
        except ValueError as exc:
            raise ValueError("provider_tool_unknown") from exc
        return cls(tool=tool, arguments=dict(arguments))

    def to_provider_dict(self) -> dict[str, object]:
        return {"tool": self.tool.value, "arguments": dict(self.arguments)}


@dataclass(frozen=True, slots=True)
class ToolPlan:
    calls: tuple[ToolCall, ...]

    @classmethod
    def parse(cls, value: object, *, max_calls: int = 32) -> "ToolPlan":
        if not isinstance(value, (list, tuple)) or not value or len(value) > max_calls:
            raise ValueError("provider_plan_invalid")
        return cls(tuple(ToolCall.parse(item) for item in value))

    @classmethod
    def parse_api_payload(cls, value: object, *, max_calls: int = 32) -> "ToolPlan":
        if not isinstance(value, Mapping) or set(value) != {"tool_calls"}:
            raise ValueError("provider_plan_schema_invalid")
        return cls.parse(value["tool_calls"], max_calls=max_calls)

    def to_safe_dict(self) -> dict[str, object]:
        return {"tool_call_count": len(self.calls), "tools": [item.tool.value for item in self.calls]}

    @staticmethod
    def api_smoke_json_schema(*, expected_path: str, expected_content: str) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "tool_calls": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "tool": {"type": "string", "const": ToolName.WRITE_FILE.value},
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "path": {"type": "string", "const": expected_path},
                                    "content": {"type": "string", "const": expected_content},
                                },
                                "required": ["path", "content"],
                            },
                        },
                        "required": ["tool", "arguments"],
                    },
                }
            },
            "required": ["tool_calls"],
        }


PRODUCT_TOOLPLAN_VERSION = "forgex.toolplan.v1"


@dataclass(frozen=True, slots=True)
class ProductToolCall:
    call_id: str
    tool_type: str
    path: str
    content: str

    @classmethod
    def parse(cls, value: object) -> "ProductToolCall":
        if not isinstance(value, Mapping) or set(value) != {"id", "type", "path", "content"}:
            raise ValueError("product_tool_call_schema_invalid")
        call_id, tool_type, path, content = (
            value["id"], value["type"], value["path"], value["content"]
        )
        if not all(isinstance(item, str) for item in (call_id, tool_type, path, content)):
            raise ValueError("product_tool_call_schema_invalid")
        if not call_id or len(call_id) > 128 or not tool_type or len(tool_type) > 64:
            raise ValueError("product_tool_call_schema_invalid")
        return cls(call_id, tool_type, path, content)

    def as_runtime_call(self) -> ToolCall:
        if self.tool_type != ToolName.WRITE_FILE.value:
            raise ValueError("product_tool_not_available")
        return ToolCall(ToolName.WRITE_FILE, {"path": self.path, "content": self.content})


@dataclass(frozen=True, slots=True)
class ProductToolPlan:
    version: str
    summary: str
    tool_calls: tuple[ProductToolCall, ...]
    final: bool

    @classmethod
    def parse(cls, value: object, *, max_calls: int = 5) -> "ProductToolPlan":
        if not isinstance(value, Mapping) or set(value) != {"version", "summary", "tool_calls", "final"}:
            raise ValueError("product_toolplan_schema_invalid")
        if value["version"] != PRODUCT_TOOLPLAN_VERSION:
            raise ValueError("product_toolplan_version_invalid")
        summary, calls, final = value["summary"], value["tool_calls"], value["final"]
        if not isinstance(summary, str) or len(summary) > 512:
            raise ValueError("product_toolplan_summary_invalid")
        if not isinstance(calls, list) or len(calls) > max_calls or not isinstance(final, bool):
            raise ValueError("product_toolplan_schema_invalid")
        parsed = tuple(ProductToolCall.parse(item) for item in calls)
        if final and parsed:
            raise ValueError("product_toolplan_final_has_calls")
        if not final and not parsed:
            raise ValueError("product_toolplan_empty_turn")
        return cls(PRODUCT_TOOLPLAN_VERSION, summary, parsed, final)

    @classmethod
    def parse_json(cls, value: str, *, max_calls: int = 5, max_bytes: int = 64 * 1024) -> "ProductToolPlan":
        import json

        if not isinstance(value, str) or len(value.encode("utf-8")) > max_bytes:
            raise ValueError("product_toolplan_json_invalid")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("product_toolplan_json_invalid") from exc
        return cls.parse(decoded, max_calls=max_calls)

    @staticmethod
    def json_schema(*, max_calls: int = 5) -> dict[str, object]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "version": {"const": PRODUCT_TOOLPLAN_VERSION},
                "summary": {"type": "string", "maxLength": 512},
                "tool_calls": {
                    "type": "array", "maxItems": max_calls,
                    "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 128},
                            "type": {"type": "string", "const": "write_file"},
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["id", "type", "path", "content"],
                    },
                },
                "final": {"type": "boolean"},
            },
            "required": ["version", "summary", "tool_calls", "final"],
        }
