"""Precise, value-free validation feedback for requirement tool arguments."""
from collections.abc import Mapping


class RequirementArgumentError(ValueError):
    def __init__(self, field, constraint, expected):
        self.argument_issue = {"field": field, "constraint": constraint, "expected": expected}
        super().__init__("product_tool_arguments_invalid")


def split_requirement_spans(checks):
    """Preserve exact contiguous coverage within the existing eight-reference cap.

    This performs no source verification. Every resulting span still passes
    strict argument validation and current-file/revision checks at execution.
    Invalid bounds, mixed fields and excessive coverage remain invalid.
    """
    if not isinstance(checks, list) or not 1 <= len(checks) <= 256:
        return checks
    normalized = []
    for check in checks:
        if not isinstance(check, Mapping) or not isinstance(check.get("evidence"), list):
            normalized.append(check)
            continue
        evidence = []
        for reference in check["evidence"]:
            if (isinstance(reference, Mapping) and set(reference) == {"path", "start_line", "end_line"}
                    and type(reference["start_line"]) is int and type(reference["end_line"]) is int
                    and 1 <= reference["start_line"] <= reference["end_line"] <= 10000000
                    and 80 < reference["end_line"] - reference["start_line"] + 1 <= 640):
                evidence.extend({"path": reference["path"], "start_line": start,
                    "end_line": min(start + 79, reference["end_line"])}
                    for start in range(reference["start_line"], reference["end_line"] + 1, 80))
            else:
                evidence.append(reference)
        # Do not discard coverage or relax the reference cap to accept a call.
        normalized.append({**check, "evidence": evidence} if len(evidence) <= 8 else check)
    return normalized


def validate_requirement_checks(checks):
    def require(condition, field, constraint, expected):
        if not condition:
            raise RequirementArgumentError(field, constraint, expected)

    require(isinstance(checks, list), "checks", "type", "A JSON array of check objects.")
    require(1 <= len(checks) <= 256, "checks", "count", "1 to 256 checks; prefer batches of at most 8.")
    for index, check in enumerate(checks):
        field = f"checks[{index}]"
        require(isinstance(check, Mapping), field, "type", "An object containing id, status and evidence.")
        require(set(check) == {"id", "status", "evidence"}, field, "fields", "Exactly id, status and evidence; no other fields.")
        require(isinstance(check["id"], str) and 1 <= len(check["id"]) <= 4096,
                field + ".id", "string", "A nonempty authoritative requirement ID, at most 4096 characters.")
        require(isinstance(check["status"], str) and check["status"] in {"implemented", "unmet"},
                field + ".status", "enum", 'Exactly "implemented" or "unmet"; review verdicts are separate.')
        evidence = check["evidence"]
        require(isinstance(evidence, list), field + ".evidence", "type", "A JSON array of file references.")
        require(len(evidence) <= 8, field + ".evidence", "count", "At most 8 references per requirement.")
        for number, reference in enumerate(evidence):
            location = f"{field}.evidence[{number}]"
            require(isinstance(reference, Mapping), location, "type", "An object containing a file reference.")
            require(isinstance(reference.get("path"), str) and 1 <= len(reference["path"]) <= 1024,
                    location + ".path", "string", "A nonempty project-relative file path, at most 1024 characters.")
            if set(reference) == {"path", "quote"}:
                require(isinstance(reference["quote"], str) and 1 <= len(reference["quote"]) <= 4096,
                        location + ".quote", "string", "An exact nonempty current quote, at most 4096 characters.")
            elif set(reference) == {"path", "start_line", "end_line"}:
                start, end = reference["start_line"], reference["end_line"]
                require(type(start) is int, location + ".start_line", "type", "An integer; strings and booleans are invalid.")
                require(type(end) is int, location + ".end_line", "type", "An integer; strings and booleans are invalid.")
                require(1 <= start <= end <= 10000000, location, "line_order", "1 <= start_line <= end_line <= 10000000.")
                require(end - start < 80, location, "line_span", "At most 80 lines inclusive. Split longer spans into separate references.")
            else:
                require(False, location, "fields", "Exactly {path,quote} OR {path,start_line,end_line}; do not mix the two forms.")
