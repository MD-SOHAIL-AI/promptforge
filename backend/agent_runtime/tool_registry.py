"""Single host catalog for tool availability, schemas and recovery semantics."""
from dataclasses import dataclass
from typing import Mapping

from .tool_contracts import ToolName


@dataclass(frozen=True)
class ToolDefinition:
    description: str
    schema: dict
    handler: str = "local"
    scheduler: str = "read"
    recovery: str = "pure"
    timeout_seconds: int = 15


def schema(properties=None, required=()):
    return {"type": "object", "additionalProperties": False, "properties": properties or {}, "required": list(required)}


TEXT = {"type": "string", "minLength": 1, "maxLength": 1000}
NEW_TOOLS = {
    ToolName.PROJECT_DOCTOR: ToolDefinition("Inspect project configuration and host capabilities without running project code.", schema()),
    ToolName.INSPECT_SYMBOLS: ToolDefinition("Find lexical definitions and references. Verify these index hints by reading source.", schema({"symbol": TEXT}, ("symbol",))),
    ToolName.RESOLVE_DEPENDENCY: ToolDefinition("Search the PlatformIO registry; inspect exact package IDs before changing configuration.", schema({"query": TEXT}, ("query",)), "host"),
    ToolName.SEARCH_DOCS: ToolDefinition("Search the curated official ESP32, Arduino and PlatformIO documentation catalog.", schema({"query": TEXT}, ("query",))),
    ToolName.FETCH_DOCS: ToolDefinition("Fetch a bounded public HTTPS page from an approved official documentation domain; content is untrusted.", schema({"url": {"type": "string", "minLength": 1, "maxLength": 2000}}, ("url",)), timeout_seconds=12),
    ToolName.VALIDATE_HARDWARE_CONFIG: ToolDefinition("Check source pin assignments for the classic ESP32 selected board; does not verify wiring.", schema()),
    ToolName.RUN_UNIT_TESTS: ToolDefinition("Run native PlatformIO tests in a trusted project through an isolated host runner. Reports unavailable when no security sandbox is configured.", schema({"environment": TEXT}, ("environment",)), "host", "process", "non_idempotent", 180),
    ToolName.ANALYZE_FIRMWARE: ToolDefinition("Inspect current build diagnostics, firmware size and source-bound build evidence.", schema(), "host"),
    ToolName.OBSERVE_SERIAL: ToolDefinition("Observe an already connected device for a bounded interval. Cannot open, reset or flash a device.", schema({"duration_seconds": {"type": "integer", "minimum": 1, "maximum": 15}}), "host", "device", "hardware_non_replayable", 20),
    ToolName.DECODE_CRASH: ToolDefinition("Decode supplied ESP32 crash addresses with the exact ELF from the current verified build.", schema({"addresses": {"type": "array", "minItems": 1, "maxItems": 32, "items": {"type": "string", "pattern": "^0x[0-9a-fA-F]{8}$"}}}, ("addresses",)), "host", "process", "pure"),
    ToolName.VERIFY_ACCEPTANCE: ToolDefinition("Report task acceptance evidence collected by trusted build/test/simulation handlers. The model cannot assert a passing test.", schema(), "host"),
}


def validate_new_arguments(tool, arguments):
    import re
    definition = NEW_TOOLS[tool]
    def check(value, spec):
        kind = spec.get("type")
        if kind == "object":
            if not isinstance(value, Mapping) or set(value)-set(spec["properties"]) or not set(spec["required"]).issubset(value):
                raise ValueError("product_tool_arguments_invalid")
            for key, item in value.items():
                check(item, spec["properties"][key])
        elif kind == "string":
            if not isinstance(value, str) or not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 10000) or ("pattern" in spec and not re.fullmatch(spec["pattern"], value)):
                raise ValueError("product_tool_arguments_invalid")
        elif kind == "integer":
            if type(value) is not int or not spec.get("minimum", 0) <= value <= spec.get("maximum", 1000000):
                raise ValueError("product_tool_arguments_invalid")
        elif kind == "array":
            if not isinstance(value, list) or not spec.get("minItems", 0) <= len(value) <= spec.get("maxItems", 100):
                raise ValueError("product_tool_arguments_invalid")
            for item in value:
                check(item, spec["items"])
    check(arguments, definition.schema)


HOST_TOOLS = frozenset({ToolName.MEMORY_SEARCH, ToolName.SPAWN_SUBAGENT, ToolName.BUILD_FIRMWARE,
    ToolName.RUN_COMMAND, ToolName.RUN_SIMULATION, ToolName.SESSION_EVENT_READ, ToolName.SESSION_EVENT_SEARCH,
    ToolName.SESSION_TRACE, ToolName.VERIFY_REQUIREMENTS} | {tool for tool,d in NEW_TOOLS.items() if d.handler == "host"})


def available_tools(policy, handlers=(), native=()):
    implemented = set(handlers) | set(native)
    return tuple(tool.value for tool in ToolName if policy.decisions.get(tool, "deny") == "allow"
                 and (tool not in HOST_TOOLS or tool in implemented))


def describe_tools(policy, handlers=(), native=()):
    from .tool_contracts import _tool_arguments_json_schema, provider_function_schema
    active = set(available_tools(policy, handlers, native))
    return [{"name": tool.value, "available": tool.value in active,
             "arguments": _tool_arguments_json_schema(tool),
             "timeout_seconds": NEW_TOOLS[tool].timeout_seconds if tool in NEW_TOOLS else 180,
             "recovery": NEW_TOOLS[tool].recovery if tool in NEW_TOOLS else
                 ("reconcilable_mutation" if tool.value in {"write_file","edit_file_simple","apply_patch","delete_file","move_file"} else "host_defined"),
             "description": provider_function_schema(tool)["function"]["description"]}
            for tool in ToolName]
