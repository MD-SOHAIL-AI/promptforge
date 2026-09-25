"""Strict, provider-neutral contracts for ForgeX Agent V3 tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class ToolName(str, Enum):
    # Workspace discovery / inspection
    LIST_FILES = "list_files"
    GLOB_FILES = "glob_files"
    GREP_SEARCH = "grep_search"
    READ_FILE = "read_file"

    # Staged workspace mutation
    WRITE_FILE = "write_file"
    EDIT_FILE_SIMPLE = "edit_file_simple"
    APPLY_PATCH = "apply_patch"
    DELETE_FILE = "delete_file"
    MOVE_FILE = "move_file"

    # Agent state / context
    UPDATE_PLAN = "update_plan"
    LOAD_SKILL = "load_skill"
    MEMORY_SEARCH = "memory_search"
    SPAWN_SUBAGENT = "spawn_subagent"
    PRESENT_FILES = "present_files"
    SESSION_EVENT_READ = "session_event_read"
    SESSION_EVENT_SEARCH = "session_event_search"
    SESSION_TRACE = "session_trace"

    # Verification. Execution is capability-gated and supplied by the host.
    BUILD_FIRMWARE = "build_firmware"
    RUN_SIMULATION = "run_simulation"
    RUN_COMMAND = "run_command"


READ_ONLY_TOOLS = frozenset(
    {
        ToolName.LIST_FILES,
        ToolName.GLOB_FILES,
        ToolName.GREP_SEARCH,
        ToolName.READ_FILE,
        ToolName.UPDATE_PLAN,
        ToolName.LOAD_SKILL,
        ToolName.MEMORY_SEARCH,
        ToolName.SPAWN_SUBAGENT,
        ToolName.SESSION_EVENT_READ,
        ToolName.SESSION_EVENT_SEARCH,
        ToolName.SESSION_TRACE,
    }
)
WRITE_TOOLS = frozenset(
    {ToolName.WRITE_FILE, ToolName.EDIT_FILE_SIMPLE, ToolName.APPLY_PATCH, ToolName.DELETE_FILE, ToolName.MOVE_FILE}
)
EXECUTION_TOOLS = frozenset(
    {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION, ToolName.RUN_COMMAND}
)


TOOL_WIRE_ALIASES: dict[str, ToolName] = {
    "read": ToolName.READ_FILE,
    "write": ToolName.WRITE_FILE,
    "edit": ToolName.EDIT_FILE_SIMPLE,
    "list": ToolName.LIST_FILES,
    "glob": ToolName.GLOB_FILES,
    "grep": ToolName.GREP_SEARCH,
    "bash": ToolName.RUN_COMMAND,
    "pwsh": ToolName.RUN_COMMAND,
    "todo_write": ToolName.UPDATE_PLAN,
    "skill": ToolName.LOAD_SKILL,
    "memory_search": ToolName.MEMORY_SEARCH,
    "subagent": ToolName.SPAWN_SUBAGENT,
    "present": ToolName.PRESENT_FILES,
    "session_event_read": ToolName.SESSION_EVENT_READ,
    "session_event_search": ToolName.SESSION_EVENT_SEARCH,
    "session_trace": ToolName.SESSION_TRACE,
    "build": ToolName.BUILD_FIRMWARE,
    "simulate": ToolName.RUN_SIMULATION,
}

TOOL_PROVIDER_NAMES: dict[ToolName, str] = {
    ToolName.READ_FILE: "read",
    ToolName.WRITE_FILE: "write",
    ToolName.EDIT_FILE_SIMPLE: "edit",
    ToolName.LIST_FILES: "list",
    ToolName.GLOB_FILES: "glob",
    ToolName.GREP_SEARCH: "grep",
    ToolName.RUN_COMMAND: "bash",
    ToolName.UPDATE_PLAN: "todo_write",
    ToolName.LOAD_SKILL: "skill",
    ToolName.MEMORY_SEARCH: "memory_search",
    ToolName.SPAWN_SUBAGENT: "subagent",
    ToolName.PRESENT_FILES: "present",
    ToolName.SESSION_EVENT_READ: "session_event_read",
    ToolName.SESSION_EVENT_SEARCH: "session_event_search",
    ToolName.SESSION_TRACE: "session_trace",
    ToolName.BUILD_FIRMWARE: "build",
    ToolName.RUN_SIMULATION: "simulate",
}

def parse_tool_name(value: str) -> ToolName:
    if value in TOOL_WIRE_ALIASES:
        return TOOL_WIRE_ALIASES[value]
    return ToolName(value)

def provider_tool_name(tool: ToolName) -> str:
    return TOOL_PROVIDER_NAMES.get(tool, tool.value)


class RuntimeClassification(str, Enum):
    PASS = "TOOL_RUNTIME_PASS"
    PROVIDER_INVALID = "TOOL_RUNTIME_PROVIDER_INVALID"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    POLICY_DENIED = "TOOL_RUNTIME_POLICY_DENIED"
    PATH_UNSAFE = "TOOL_RUNTIME_PATH_UNSAFE"
    FILE_NOT_FOUND = "TOOL_RUNTIME_FILE_NOT_FOUND"
    CONTENT_INVALID = "TOOL_RUNTIME_CONTENT_INVALID"
    EXTRA_CHANGES = "TOOL_RUNTIME_EXTRA_CHANGES"
    NO_CHANGES = "TOOL_RUNTIME_NO_CHANGES"
    CHANGESET_CREATED = "TOOL_RUNTIME_CHANGESET_CREATED"
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
    STAGE_CREATED = "changes.stage.created"
    CHANGESET_CREATED = "changeset.created"
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
        payload: dict[str, object] = {
            "event_type": self.event_type.value,
            "sequence": self.sequence,
        }
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
            tool = parse_tool_name(raw_tool)
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
        return {
            "tool_call_count": len(self.calls),
            "tools": [item.tool.value for item in self.calls],
        }

    @staticmethod
    def api_smoke_json_schema(
        *, expected_path: str, expected_content: str
    ) -> dict[str, object]:
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
                            "tool": {
                                "type": "string",
                                "const": ToolName.WRITE_FILE.value,
                            },
                            "arguments": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "path": {"type": "string", "const": expected_path},
                                    "content": {
                                        "type": "string",
                                        "const": expected_content,
                                    },
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


# The legacy version is intentionally still accepted so old verified templates,
# saved fixtures, and API responses do not break during the V2 -> V3 cutover.
PRODUCT_TOOLPLAN_VERSION = "forgex.toolplan.v2"
LEGACY_PRODUCT_TOOLPLAN_VERSION = "forgex.toolplan.v1"


@dataclass(frozen=True, slots=True)
class ProductToolCall:
    call_id: str
    tool_type: str
    arguments: Mapping[str, object] = field(default_factory=dict, repr=False)

    @property
    def path(self) -> str:
        value = self.arguments.get("path")
        return value if isinstance(value, str) else ""

    @property
    def content(self) -> str:
        value = self.arguments.get("content")
        return value if isinstance(value, str) else ""

    @classmethod
    def parse(cls, value: object) -> "ProductToolCall":
        if not isinstance(value, Mapping):
            raise ValueError("product_tool_call_schema_invalid")
        # V1 compatibility: {id,type,path,content}
        if set(value) == {"id", "type", "path", "content"}:
            call_id, tool_type, path, content = (
                value["id"],
                value["type"],
                value["path"],
                value["content"],
            )
            if not all(
                isinstance(item, str) for item in (call_id, tool_type, path, content)
            ):
                raise ValueError("product_tool_call_schema_invalid")
            return cls._validated(
                call_id, tool_type, {"path": path, "content": content}
            )
        # V3 canonical: {id,type,arguments}
        if set(value) != {"id", "type", "arguments"}:
            raise ValueError("product_tool_call_schema_invalid")
        call_id, tool_type, arguments = value["id"], value["type"], value["arguments"]
        if (
            not isinstance(call_id, str)
            or not isinstance(tool_type, str)
            or not isinstance(arguments, Mapping)
        ):
            raise ValueError("product_tool_call_schema_invalid")
        return cls._validated(call_id, tool_type, dict(arguments))

    @classmethod
    def _validated(
        cls, call_id: str, tool_type: str, arguments: Mapping[str, object]
    ) -> "ProductToolCall":
        if not call_id or len(call_id) > 128 or not tool_type or len(tool_type) > 64:
            raise ValueError("product_tool_call_schema_invalid")
        try:
            tool = parse_tool_name(tool_type)
        except ValueError as exc:
            raise ValueError("product_tool_not_available") from exc
        _validate_product_tool_arguments(tool, arguments)
        # Provider aliases are wire compatibility only. Runtime plans always
        # carry the canonical enum spelling so policy allowlists cannot diverge.
        return cls(call_id, tool.value, dict(arguments))

    def as_runtime_call(self) -> ToolCall:
        try:
            tool = ToolName(self.tool_type)
        except ValueError as exc:
            raise ValueError("product_tool_not_available") from exc
        return ToolCall(tool, dict(self.arguments))

    def to_provider_dict(self) -> dict[str, object]:
        return {
            "id": self.call_id,
            "type": self.tool_type,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ProductToolPlan:
    version: str
    summary: str
    tool_calls: tuple[ProductToolCall, ...]
    final: bool

    @classmethod
    def parse(cls, value: object, *, max_calls: int = 5) -> "ProductToolPlan":
        if not isinstance(value, Mapping) or set(value) != {
            "version",
            "summary",
            "tool_calls",
            "final",
        }:
            raise ValueError("product_toolplan_schema_invalid")
        version = value["version"]
        if version not in {PRODUCT_TOOLPLAN_VERSION, LEGACY_PRODUCT_TOOLPLAN_VERSION}:
            raise ValueError("product_toolplan_version_invalid")
        summary, calls, final = value["summary"], value["tool_calls"], value["final"]
        if not isinstance(summary, str) or len(summary) > (
            16000 if final is True else 512
        ):
            raise ValueError("product_toolplan_summary_invalid")
        if (
            not isinstance(calls, list)
            or len(calls) > max_calls
            or not isinstance(final, bool)
        ):
            raise ValueError("product_toolplan_schema_invalid")
        parsed = tuple(ProductToolCall.parse(item) for item in calls)
        if final and parsed:
            raise ValueError("product_toolplan_final_has_calls")
        if not final and not parsed:
            raise ValueError("product_toolplan_empty_turn")
        return cls(str(version), summary, parsed, final)

    @classmethod
    def parse_json(
        cls, value: str, *, max_calls: int = 5, max_bytes: int = 64 * 1024
    ) -> "ProductToolPlan":
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
                "version": {
                    "enum": [PRODUCT_TOOLPLAN_VERSION, LEGACY_PRODUCT_TOOLPLAN_VERSION]
                },
                "summary": {"type": "string", "maxLength": 16000},
                "tool_calls": {
                    "type": "array",
                    "maxItems": max_calls,
                    "items": {
                        "oneOf": [_tool_call_json_schema(tool) for tool in ToolName]
                    },
                },
                "final": {"type": "boolean"},
            },
            "required": ["version", "summary", "tool_calls", "final"],
        }




def provider_function_schema(tool: ToolName) -> dict[str, object]:
    descriptions = {
        ToolName.READ_FILE: "Read a UTF-8 project file from the contained workspace.",
        ToolName.WRITE_FILE: "Stage a complete file write inside the ForgeX ChangeSet workspace.",
        ToolName.EDIT_FILE_SIMPLE: "Stage an exact text replacement in one project file.",
        ToolName.LIST_FILES: "List contained project files.",
        ToolName.GLOB_FILES: "Find project files by glob pattern.",
        ToolName.GREP_SEARCH: "Search project source text.",
        ToolName.RUN_COMMAND: "Run a bounded verification command under ForgeX host policy.",
        ToolName.UPDATE_PLAN: "Update the durable public execution plan.",
        ToolName.LOAD_SKILL: "Load an installed ForgeX skill by name.",
        ToolName.MEMORY_SEARCH: "Search verified project memory.",
        ToolName.SPAWN_SUBAGENT: "Delegate a bounded explore/review/verify task.",
        ToolName.PRESENT_FILES: "Declare existing contained project files as user deliverables.",
        ToolName.SESSION_EVENT_READ: "Read a bounded range of this Agent session\'s durable runtime events.",
        ToolName.SESSION_EVENT_SEARCH: "Search this Agent session\'s durable public runtime history.",
        ToolName.SESSION_TRACE: "Inspect a compact public execution trace for this Agent session.",
        ToolName.BUILD_FIRMWARE: "Build the staged embedded project with PlatformIO.",
        ToolName.RUN_SIMULATION: "Run the configured simulator as a verification step.",
        ToolName.APPLY_PATCH: "Stage a precise unified patch inside the contained workspace.",
        ToolName.DELETE_FILE: "Stage deletion of one file. Never deletes active workspace files before ChangeSet approval.",
        ToolName.MOVE_FILE: "Stage moving one text file to an unused destination; applied as a reviewed delete and create pair.",
    }
    return {
        "type": "function",
        "function": {
            "name": provider_tool_name(tool),
            "description": descriptions.get(tool, f"ForgeX tool: {tool.value}"),
            "parameters": _tool_arguments_json_schema(tool),
            "strict": True,
        },
    }

def _tool_call_json_schema(tool: ToolName) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 128},
            "type": {"const": tool.value},
            "arguments": _tool_arguments_json_schema(tool),
        },
        "required": ["id", "type", "arguments"],
    }


def _tool_arguments_json_schema(tool: ToolName) -> dict[str, object]:
    string = {"type": "string", "minLength": 1, "maxLength": 4096}
    path = {"type": "string", "minLength": 1, "maxLength": 1024}
    schemas: dict[ToolName, dict[str, object]] = {
        ToolName.LIST_FILES: {"properties": {"path": path}, "required": []},
        ToolName.GLOB_FILES: {
            "properties": {"pattern": string, "path": path},
            "required": ["pattern"],
        },
        ToolName.GREP_SEARCH: {
            "properties": {
                "query": string,
                "path": path,
                "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["query"],
        },
        ToolName.READ_FILE: {
            "properties": {
                "path": path,
                "offset": {"type": "integer", "minimum": 1, "maximum": 10000000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            "required": ["path"],
        },
        ToolName.WRITE_FILE: {
            "properties": {
                "path": path,
                "content": {"type": "string", "maxLength": 65536},
            },
            "required": ["path", "content"],
        },
        ToolName.EDIT_FILE_SIMPLE: {
            "properties": {
                "path": path,
                "old_text": {"type": "string", "minLength": 1, "maxLength": 65536},
                "new_text": {"type": "string", "maxLength": 65536},
            },
            "required": ["path", "old_text", "new_text"],
        },
        ToolName.APPLY_PATCH: {
            "properties": {
                "path": path,
                "patch": {"type": "string", "minLength": 1, "maxLength": 65536},
            },
            "required": ["path", "patch"],
        },
        ToolName.DELETE_FILE: {"properties": {"path": path}, "required": ["path"]},
        ToolName.MOVE_FILE: {"properties": {"path": path, "destination": path}, "required": ["path", "destination"]},
        ToolName.UPDATE_PLAN: {
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 32,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string", "minLength": 1, "maxLength": 300},
                            "status": {"enum": ["pending", "in_progress", "completed", "failed", "skipped", "cancelled"]},
                            "verification": {"enum": ["inspection", "edit", "build", "simulation"]},
                        },
                        "required": ["text", "status"],
                    },
                }
            },
            "required": ["steps"],
        },
        ToolName.LOAD_SKILL: {"properties": {"name": {"type": "string", "minLength": 1, "maxLength": 128}}, "required": ["name"]},
        ToolName.MEMORY_SEARCH: {"properties": {"query": string, "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["query"]},
        ToolName.SPAWN_SUBAGENT: {"properties": {"role": {"enum": ["explore", "review", "verify"]}, "task": {"type": "string", "minLength": 1, "maxLength": 4000}}, "required": ["role", "task"]},
        ToolName.PRESENT_FILES: {"properties": {"files": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "object", "additionalProperties": False, "properties": {"path": path, "description": {"type": "string", "maxLength": 300}}, "required": ["path"]}}}, "required": ["files"]},
        ToolName.SESSION_EVENT_READ: {"properties": {"after_sequence": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
        ToolName.SESSION_EVENT_SEARCH: {"properties": {"query": {"type": "string", "minLength": 1, "maxLength": 300}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": ["query"]},
        ToolName.SESSION_TRACE: {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": []},
        ToolName.BUILD_FIRMWARE: {"properties": {"environment": string}, "required": []},
        ToolName.RUN_SIMULATION: {"properties": {"timeout_seconds": {"type": "number", "minimum": 1, "maximum": 120}}, "required": []},
        ToolName.RUN_COMMAND: {"properties": {"argv": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string", "minLength": 1, "maxLength": 512}}, "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 180}}, "required": ["argv"]},
    }
    selected = schemas[tool]
    return {
        "type": "object",
        "additionalProperties": False,
        **selected,
    }


def _validate_product_tool_arguments(
    tool: ToolName, arguments: Mapping[str, object]
) -> None:
    required: dict[ToolName, frozenset[str]] = {
        ToolName.GLOB_FILES: frozenset({"pattern"}),
        ToolName.GREP_SEARCH: frozenset({"query"}),
        ToolName.READ_FILE: frozenset({"path"}),
        ToolName.WRITE_FILE: frozenset({"path", "content"}),
        ToolName.EDIT_FILE_SIMPLE: frozenset({"path", "old_text", "new_text"}),
        ToolName.APPLY_PATCH: frozenset({"path", "patch"}),
        ToolName.DELETE_FILE: frozenset({"path"}),
        ToolName.MOVE_FILE: frozenset({"path", "destination"}),
        ToolName.UPDATE_PLAN: frozenset({"steps"}),
        ToolName.LOAD_SKILL: frozenset({"name"}),
        ToolName.MEMORY_SEARCH: frozenset({"query"}),
        ToolName.SPAWN_SUBAGENT: frozenset({"role", "task"}),
        ToolName.PRESENT_FILES: frozenset({"files"}),
        ToolName.SESSION_EVENT_SEARCH: frozenset({"query"}),
        ToolName.RUN_COMMAND: frozenset({"argv"}),
    }
    properties = _tool_arguments_json_schema(tool).get("properties", {})
    allowed = set(properties) if isinstance(properties, Mapping) else set()
    keys = set(arguments)
    if not required.get(tool, frozenset()).issubset(keys) or keys - allowed:
        raise ValueError("product_tool_arguments_invalid")
    for key in ("path", "destination", "pattern", "query", "name", "environment", "role", "task"):
        if key in arguments and (
            not isinstance(arguments[key], str) or not str(arguments[key]).strip()
        ):
            raise ValueError("product_tool_arguments_invalid")
    for key in ("content", "old_text", "new_text", "patch"):
        if key in arguments and not isinstance(arguments[key], str):
            raise ValueError("product_tool_arguments_invalid")
    if tool is ToolName.EDIT_FILE_SIMPLE and not str(arguments.get("old_text", "")):
        raise ValueError("product_tool_arguments_invalid")
    if tool is ToolName.APPLY_PATCH and not str(arguments.get("patch", "")).strip():
        raise ValueError("product_tool_arguments_invalid")
    if tool is ToolName.RUN_COMMAND:
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not argv or any(
            not isinstance(item, str) or not item for item in argv
        ):
            raise ValueError("product_tool_arguments_invalid")
    if tool is ToolName.UPDATE_PLAN and not isinstance(arguments.get("steps"), list):
        raise ValueError("product_tool_arguments_invalid")
    if tool is ToolName.PRESENT_FILES:
        files = arguments.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 32:
            raise ValueError("product_tool_arguments_invalid")
        for item in files:
            if not isinstance(item, Mapping) or set(item) - {"path", "description"} or "path" not in item:
                raise ValueError("product_tool_arguments_invalid")
            if not isinstance(item.get("path"), str) or not str(item.get("path")).strip():
                raise ValueError("product_tool_arguments_invalid")
            if "description" in item and not isinstance(item.get("description"), str):
                raise ValueError("product_tool_arguments_invalid")
