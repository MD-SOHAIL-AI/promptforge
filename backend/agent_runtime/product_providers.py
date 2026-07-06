"""Product planner contracts and the deterministic development provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
from ..provider_runtime.templates import match_template


PRODUCT_SMOKE_PATH = "FORGEX_AGENT_RUNTIME_SMOKE.txt"
PRODUCT_SMOKE_CONTENT = "ForgeX product agent runtime completed.\n"


class ProductPlanner(Protocol):
    provider_id: str
    model_id: str

    def request_turn(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        run_id: str,
        turn: int,
    ) -> ProductToolPlan: ...


class FakeProductPlanner:
    provider_id = "fake_planner"
    model_id = "forgex-fake-planner-v1"

    def request_turn(
        self,
        *,
        task: str,
        tool_results: Sequence[Mapping[str, object]],
        allowed_tools: Sequence[str],
        run_id: str,
        turn: int,
    ) -> ProductToolPlan:
        del task, run_id
        if "write_file" not in allowed_tools:
            raise ValueError("fake_planner_write_unavailable")
        if turn == 1:
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "Create the bounded product-runtime smoke artifact.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "write_file",
                    "path": PRODUCT_SMOKE_PATH,
                    "content": PRODUCT_SMOKE_CONTENT,
                }],
                "final": False,
            })
        if not tool_results or tool_results[-1].get("status") != "completed":
            raise ValueError("fake_planner_missing_tool_result")
        return ProductToolPlan.parse({
            "version": PRODUCT_TOOLPLAN_VERSION,
            "summary": "Completed.",
            "tool_calls": [],
            "final": True,
        })


class VerifiedTemplatePlanner:
    provider_id = "verified_template"
    model_id = "forgex-verified-templates-v1"
    provider_kind = "template_provider"
    transport_name = "in_process_verified_template"

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
        if "write_file" not in allowed_tools:
            raise ValueError("template_write_unavailable")
        template = match_template(task)
        if template is None:
            raise ValueError("template_not_recognized")
        if turn == 1:
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": f"Generate verified {template.display_name} template.",
                "tool_calls": [
                    {"id": f"template_{index}", "type": "write_file", "path": path, "content": content}
                    for index, (path, content) in enumerate(template.files, start=1)
                ],
                "final": False,
            })
        if len(tool_results) != len(template.files) or any(item.get("status") != "completed" for item in tool_results):
            raise ValueError("template_write_failed")
        return ProductToolPlan.parse({
            "version": PRODUCT_TOOLPLAN_VERSION,
            "summary": "Verified template generation completed.",
            "tool_calls": [],
            "final": True,
        })
