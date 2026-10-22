"""The tool-free requirement review response contract."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from .tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan


def requirement_review_schema(requirement_ids: Sequence[str]) -> dict:
    ids = tuple(requirement_ids)
    if not ids or any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("SEMANTIC_REVIEW_REQUIREMENTS_INVALID")
    return {
        "type": "object", "additionalProperties": False, "required": ["checks"],
        "properties": {"checks": {
            "type": "array", "minItems": len(ids), "maxItems": len(ids),
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["id", "verdict", "reason"],
                "properties": {
                    "id": {"type": "string", "enum": list(ids)},
                    "verdict": {"type": "string", "enum": ["satisfied", "unverified"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
            },
        }},
    }


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("SEMANTIC_REVIEW_INVALID")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("SEMANTIC_REVIEW_INVALID")


def _json_value(content):
    if isinstance(content, str):
        if len(content) > 64_000:
            raise ValueError("SEMANTIC_REVIEW_INVALID")
        candidate = content.strip()
        if candidate.startswith("```json\n") and candidate.endswith("\n```"):
            candidate = candidate[8:-4].strip()
        try:
            return json.loads(candidate, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise ValueError("SEMANTIC_REVIEW_INVALID") from exc
    return content


def parse_requirement_review(content, requirement_ids: Sequence[str]) -> ProductToolPlan:
    """Validate every verdict; accept a legacy envelope only when it is final and tool-free."""
    requirement_review_schema(requirement_ids)
    value = _json_value(content)
    if isinstance(value, Mapping) and set(value) == {"version", "summary", "tool_calls", "final"}:
        try:
            plan = ProductToolPlan.parse(value)
        except ValueError as exc:
            raise ValueError("SEMANTIC_REVIEW_INVALID") from exc
        if not plan.final or plan.tool_calls:
            raise ValueError("SEMANTIC_REVIEW_INVALID")
        value = _json_value(plan.summary)
    if not isinstance(value, Mapping) or set(value) != {"checks"} or not isinstance(value["checks"], list):
        raise ValueError("SEMANTIC_REVIEW_INVALID")
    expected, seen = set(requirement_ids), set()
    for check in value["checks"]:
        if (not isinstance(check, Mapping) or set(check) != {"id", "verdict", "reason"}
                or not isinstance(check["id"], str) or check["id"] not in expected or check["id"] in seen
                or not isinstance(check["verdict"], str) or check["verdict"] not in {"satisfied", "unverified"}
                or not isinstance(check["reason"], str) or not 1 <= len(check["reason"]) <= 2000):
            raise ValueError("SEMANTIC_REVIEW_INVALID")
        seen.add(check["id"])
    if seen != expected:
        raise ValueError("SEMANTIC_REVIEW_INCOMPLETE")
    summary = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(summary) > 16_000:
        raise ValueError("SEMANTIC_REVIEW_INVALID")
    return ProductToolPlan(PRODUCT_TOOLPLAN_VERSION, summary, (), True)
